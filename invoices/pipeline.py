"""Glue between an uploaded Invoice and the OCR + extraction services."""
from datetime import date

import json

import requests
from django.db import transaction

from services import paypal_client
from services.extraction import extract_invoice
from services.ocr import extract_text
from services.paypal_client import PayPalError

from .models import Invoice, LineItem


def run_extraction(invoice):
    """Fill `invoice` and its line items from its uploaded PDF.

    Raises services.ocr.OCRError or services.extraction.ExtractionError on failure;
    the caller decides what to show the user.
    """
    ocr = extract_text(invoice.source_file.path)
    result = extract_invoice(ocr["text"])
    data = result["data"]

    with transaction.atomic():
        invoice.ocr_method = ocr["method"]
        invoice.client_email = data["client_email"]
        invoice.client_name = data["client_name"] or ""
        invoice.currency = data["currency"]
        invoice.due_date = date.fromisoformat(data["due_date"]) if data["due_date"] else None
        invoice.note = data["note"] or ""
        invoice.warnings = result["warnings"]
        invoice.save()
        for item in data["items"]:
            LineItem.objects.create(
                invoice=invoice,
                name=item["name"][:200],
                quantity=item["quantity"],
                unit_price=item["unit_price"],
            )


def _friendly_paypal_error(e):
    """Turn a PayPalError into a short message a user can act on."""
    body = e.body if isinstance(e.body, str) else ""
    if "INR_FOREIGN_CURRENCY_BLOCKED" in body:
        return "PayPal can't send invoices to customers in India. Use a client email outside India (for the demo, your US sandbox account)."
    if e.status == 401:
        return "PayPal rejected the credentials. Check PAYPAL_CLIENT_ID and PAYPAL_SECRET in .env."
    if e.status == 403:
        return "This PayPal app isn't allowed to create invoices. Make sure Invoicing is enabled on the app."
    try:
        err = json.loads(body)
        issue = (err.get("details") or [{}])[0].get("issue", "")
        message = err.get("message", "")
        return f"PayPal rejected the invoice: {message} {('(' + issue + ')') if issue else ''}".strip()
    except (ValueError, AttributeError, IndexError):
        return f"PayPal request failed during {e.step} (HTTP {e.status})."


def _discard_draft(invoice_id):
    if not invoice_id:
        return
    try:
        paypal_client.delete_invoice(invoice_id)
    except Exception:  # best effort: a leftover draft is harmless
        pass


def send_invoice_to_paypal(invoice):
    """Create and send the invoice on PayPal. Returns True on success.

    On failure the invoice stays in the review state with `error_message` set, and any
    half-created PayPal draft is deleted so a retry doesn't leave duplicates.
    """
    created_id = ""
    try:
        created_id = paypal_client.create_invoice(invoice.to_paypal_data())
        payer_url = paypal_client.send_invoice(created_id) or ""
    except PayPalError as e:
        _discard_draft(created_id)
        invoice.error_message = _friendly_paypal_error(e)
    except requests.RequestException:
        _discard_draft(created_id)
        invoice.error_message = "Could not reach PayPal. Check your internet connection and try again."
    except KeyError:
        invoice.error_message = "PayPal credentials are missing. Set PAYPAL_CLIENT_ID and PAYPAL_SECRET in .env."
    else:
        invoice.paypal_invoice_id = created_id
        invoice.payer_url = payer_url
        invoice.paypal_status = "SENT"
        invoice.state = Invoice.State.SENT
        invoice.error_message = ""
        invoice.save()
        return True
    invoice.save(update_fields=["error_message"])
    return False