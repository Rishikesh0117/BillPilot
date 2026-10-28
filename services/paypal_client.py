"""PayPal Invoicing client for BillPilot (sandbox).

Invoice data shape used across the app:
    {
        "client_email": "buyer@example.com",
        "client_name": "Acme Corp",          # optional
        "currency": "USD",                   # optional, default USD
        "due_date": "2026-12-01",            # optional, YYYY-MM-DD
        "note": "Thanks for your business",  # optional
        "items": [{"name": "Design work", "quantity": 2, "unit_price": 50.0}],
    }

Config comes from environment variables (loaded from .env):
    PAYPAL_CLIENT_ID, PAYPAL_SECRET, INVOICER_EMAIL (optional)
"""
import os
import time

import requests
from dotenv import load_dotenv

load_dotenv()

BASE = os.getenv("PAYPAL_BASE_URL", "https://api-m.sandbox.paypal.com")
TIMEOUT = 30

_token = {"value": None, "expires_at": 0.0}


class PayPalError(Exception):
    def __init__(self, step, status, body):
        super().__init__(f"{step} failed ({status}): {body}")
        self.step, self.status, self.body = step, status, body


def _check(resp, step):
    if not resp.ok:
        raise PayPalError(step, resp.status_code, resp.text)
    return resp


def _headers():
    now = time.time()
    if not _token["value"] or now >= _token["expires_at"] - 60:
        r = requests.post(
            f"{BASE}/v1/oauth2/token",
            auth=(os.environ["PAYPAL_CLIENT_ID"], os.environ["PAYPAL_SECRET"]),
            data={"grant_type": "client_credentials"},
            timeout=TIMEOUT,
        )
        data = _check(r, "auth").json()
        _token["value"] = data["access_token"]
        _token["expires_at"] = now + int(data.get("expires_in", 300))
    return {
        "Authorization": f"Bearer {_token['value']}",
        "Content-Type": "application/json",
    }


def _money(currency, amount):
    return {"currency_code": currency, "value": f"{float(amount):.2f}"}


def create_invoice(data):
    """Create a draft invoice. Returns the invoice ID."""
    currency = data.get("currency", "USD")
    detail = {"currency_code": currency}
    if data.get("note"):
        detail["note"] = data["note"]
    if data.get("due_date"):
        detail["payment_term"] = {
            "term_type": "DUE_ON_DATE_SPECIFIED",
            "due_date": data["due_date"],
        }

    billing = {"email_address": data["client_email"]}
    if data.get("client_name"):
        billing["name"] = {"given_name": data["client_name"]}

    payload = {
        "detail": detail,
        "primary_recipients": [{"billing_info": billing}],
        "items": [
            {
                "name": item["name"],
                "quantity": str(item.get("quantity", 1)),
                "unit_amount": _money(currency, item["unit_price"]),
            }
            for item in data["items"]
        ],
    }
    invoicer = os.getenv("INVOICER_EMAIL")
    if invoicer:
        payload["invoicer"] = {"email_address": invoicer}

    r = requests.post(
        f"{BASE}/v2/invoicing/invoices", json=payload, headers=_headers(), timeout=TIMEOUT
    )
    href = _check(r, "create").json()["href"]
    return href.rsplit("/", 1)[-1]


def send_invoice(invoice_id):
    """Send a draft invoice. Returns the payer-facing invoice URL."""
    r = requests.post(
        f"{BASE}/v2/invoicing/invoices/{invoice_id}/send",
        json={"send_to_recipient": True},
        headers=_headers(),
        timeout=TIMEOUT,
    )
    return _check(r, "send").json().get("href")


def get_invoice(invoice_id):
    r = requests.get(
        f"{BASE}/v2/invoicing/invoices/{invoice_id}", headers=_headers(), timeout=TIMEOUT
    )
    return _check(r, "get").json()


def get_status(invoice_id):
    """Returns e.g. DRAFT, SENT, PAID, CANCELLED."""
    return get_invoice(invoice_id)["status"]


def send_reminder(invoice_id, note="Friendly reminder: this invoice is still unpaid."):
    r = requests.post(
        f"{BASE}/v2/invoicing/invoices/{invoice_id}/remind",
        json={"subject": "Payment reminder", "note": note, "send_to_recipient": True},
        headers=_headers(),
        timeout=TIMEOUT,
    )
    _check(r, "remind")
    return True


def create_and_send(data):
    """Convenience: create, send, return (invoice_id, payer_url)."""
    invoice_id = create_invoice(data)
    return invoice_id, send_invoice(invoice_id)


if __name__ == "__main__":
    demo = {
        "client_email": os.environ["PAYER_EMAIL"],
        "client_name": "Test Client",
        "items": [{"name": "Test service", "quantity": 1, "unit_price": 10}],
    }
    inv_id, url = create_and_send(demo)
    print(inv_id, url, get_status(inv_id))