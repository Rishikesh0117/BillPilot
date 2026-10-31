"""Invoice text → structured invoice data (for services.paypal_client).

Works with any OpenAI-compatible chat endpoint. Configure in .env:
    LLM_BASE_URL=https://.../v1      (the endpoint's base URL, without /chat/completions)
    LLM_API_KEY=...
    LLM_MODEL=...                     (exact model ID from your provider)

Usage:
    from services.extraction import extract_invoice
    result = extract_invoice(ocr_text)
    result["data"]      # dict ready for paypal_client.create_invoice (after user review)
    result["warnings"]  # list of strings to show on the review page
"""
import json
import os
import re
from datetime import datetime

import requests
from dotenv import load_dotenv

load_dotenv()
TIMEOUT = 90
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

SYSTEM_PROMPT = """You extract invoice data from text. Reply with ONLY one JSON object, no markdown, no commentary.

Schema:
{
  "client_email": string,        // email of the party BEING BILLED (not the vendor/seller)
  "client_name": string | null,  // name of the party being billed
  "currency": string,            // ISO 4217 code, e.g. USD, EUR, GBP. Map $ to USD, € to EUR, £ to GBP
  "due_date": string | null,     // YYYY-MM-DD, or null if the document gives none
  "note": string | null,         // short payment note/terms if present, else null
  "items": [ { "name": string, "quantity": number, "unit_price": number } ],
  "total": number | null         // grand total stated on the document, or null
}

Rules:
- unit_price is the price of ONE unit, not the line total. quantity defaults to 1 only if the document shows no quantity.
- Numbers are plain numbers: no currency symbols, no thousands separators.
- Never invent data. If a field is not in the text, use null (client_email and items are required: use "" and [] if missing).
- Do not include tax, subtotal, or total lines as items."""


class ExtractionError(Exception):
    pass


def _call_llm(messages):
    try:
        base = os.environ["LLM_BASE_URL"].rstrip("/")
        key = os.environ["LLM_API_KEY"]
        model = os.environ["LLM_MODEL"]
    except KeyError as e:
        raise ExtractionError(f"Missing {e.args[0]} in environment/.env") from e

    r = requests.post(
        f"{base}/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"model": model, "messages": messages, "temperature": 0},
        timeout=TIMEOUT,
    )
    if not r.ok:
        raise ExtractionError(f"LLM request failed ({r.status_code}): {r.text[:300]}")
    try:
        return r.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, ValueError) as e:
        raise ExtractionError(f"Unexpected LLM response shape: {r.text[:300]}") from e


def _parse_json(text):
    text = re.sub(r"```(?:json)?", "", text or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ExtractionError("LLM reply contained no JSON object.")
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as e:
        raise ExtractionError(f"LLM returned invalid JSON: {e}") from e


def _num(value, field):
    try:
        if isinstance(value, str):
            value = value.replace(",", "").strip()
        return float(value)
    except (TypeError, ValueError) as e:
        raise ExtractionError(f"{field} is not a number: {value!r}") from e


def validate(raw):
    """Clean and check the LLM output. Returns (data, warnings). Raises ExtractionError if unusable."""
    warnings = []

    email = (raw.get("client_email") or "").strip()
    if not EMAIL_RE.match(email):
        raise ExtractionError("No valid client email found in the document.")

    currency = (raw.get("currency") or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", currency):
        warnings.append("Currency not found; defaulted to USD.")
        currency = "USD"

    due = raw.get("due_date")
    if due:
        try:
            datetime.strptime(due, "%Y-%m-%d")
        except (TypeError, ValueError):
            warnings.append(f"Ignored invalid due date: {due!r}")
            due = None
    else:
        due = None

    items = []
    for i, it in enumerate(raw.get("items") or [], 1):
        name = str(it.get("name") or "").strip()
        if not name:
            raise ExtractionError(f"Item {i} has no name.")
        qty = _num(it.get("quantity", 1), f"Item {i} quantity")
        price = _num(it.get("unit_price"), f"Item {i} unit price")
        if qty <= 0 or price < 0:
            raise ExtractionError(f"Item {i} has an invalid quantity or price.")
        items.append({"name": name, "quantity": int(qty) if qty == int(qty) else qty, "unit_price": round(price, 2)})
    if not items:
        raise ExtractionError("No line items found in the document.")

    computed = round(sum(i["quantity"] * i["unit_price"] for i in items), 2)
    total = raw.get("total")
    if total is not None:
        try:
            if abs(float(total) - computed) > 0.01:
                warnings.append(f"Items add up to {computed:.2f} but the document total is {float(total):.2f}. Please check.")
        except (TypeError, ValueError):
            pass

    data = {
        "client_email": email,
        "client_name": (raw.get("client_name") or "").strip() or None,
        "currency": currency,
        "due_date": due,
        "note": (raw.get("note") or "").strip() or None,
        "items": items,
    }
    return data, warnings


def extract_invoice(text):
    """Returns {"data": {...}, "warnings": [...]}. Raises ExtractionError on failure."""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Invoice text:\n\n{text}"},
    ]
    last_error = None
    for _ in range(2):  # one retry on bad JSON / failed validation
        reply = _call_llm(messages)
        try:
            data, warnings = validate(_parse_json(reply))
            return {"data": data, "warnings": warnings}
        except ExtractionError as e:
            last_error = e
            messages += [
                {"role": "assistant", "content": reply},
                {"role": "user", "content": f"That was not usable ({e}). Reply again with only the corrected JSON object."},
            ]
    raise last_error