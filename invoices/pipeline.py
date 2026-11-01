"""Glue between an uploaded Invoice and the OCR + extraction services."""
from datetime import date

from django.db import transaction

from services.extraction import extract_invoice
from services.ocr import extract_text

from .models import LineItem


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