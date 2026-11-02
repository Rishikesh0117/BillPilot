from pathlib import Path
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from services.extraction import ExtractionError

from .models import Invoice

SAMPLE = Path(__file__).resolve().parent.parent / "samples" / "invoice_classic_usd.pdf"

FAKE_RESULT = {
    "data": {
        "client_email": "billing@brightwave.example.com",
        "client_name": "Brightwave Labs",
        "currency": "USD",
        "due_date": "2026-11-30",
        "note": "Payment due within 30 days.",
        "items": [
            {"name": "Brand identity design", "quantity": 1, "unit_price": 1200.0},
            {"name": "Logo variations", "quantity": 3, "unit_price": 150.0},
        ],
    },
    "warnings": ["Example warning"],
}


def sample_upload(name="invoice.pdf"):
    return SimpleUploadedFile(name, SAMPLE.read_bytes(), content_type="application/pdf")


@override_settings(MEDIA_ROOT="/tmp/billpilot_test_media")
class UploadFlowTests(TestCase):
    def test_upload_creates_invoice_and_items(self):
        with patch("invoices.pipeline.extract_invoice", return_value=FAKE_RESULT):
            resp = self.client.post(reverse("upload"), {"file": sample_upload()})
        invoice = Invoice.objects.get()
        self.assertRedirects(resp, reverse("invoice_review", args=[invoice.pk]))
        self.assertEqual(invoice.client_email, "billing@brightwave.example.com")
        self.assertEqual(invoice.items.count(), 2)
        self.assertEqual(str(invoice.total), "1650.00")
        self.assertEqual(invoice.ocr_method, "native")
        self.assertEqual(invoice.warnings, ["Example warning"])

    def test_detail_page_renders(self):
        with patch("invoices.pipeline.extract_invoice", return_value=FAKE_RESULT):
            self.client.post(reverse("upload"), {"file": sample_upload()})
        invoice = Invoice.objects.get()
        resp = self.client.get(reverse("invoice_detail", args=[invoice.pk]))
        self.assertContains(resp, "Brightwave Labs")
        self.assertContains(resp, "Example warning")
        self.assertContains(self.client.get(reverse("invoice_list")), "Brightwave Labs")

    def test_extraction_failure_leaves_no_record(self):
        with patch("invoices.pipeline.extract_invoice", side_effect=ExtractionError("boom")):
            resp = self.client.post(reverse("upload"), {"file": sample_upload()})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Could not read this document")
        self.assertEqual(Invoice.objects.count(), 0)

    def test_rejects_non_pdf(self):
        bad = SimpleUploadedFile("notes.txt", b"hello", content_type="text/plain")
        resp = self.client.post(reverse("upload"), {"file": bad})
        self.assertContains(resp, "upload a PDF")
        self.assertEqual(Invoice.objects.count(), 0)

    def test_rejects_fake_pdf(self):
        bad = SimpleUploadedFile("fake.pdf", b"not really a pdf", content_type="application/pdf")
        resp = self.client.post(reverse("upload"), {"file": bad})
        self.assertContains(resp, "valid PDF")

    def test_to_paypal_data_shape(self):
        with patch("invoices.pipeline.extract_invoice", return_value=FAKE_RESULT):
            self.client.post(reverse("upload"), {"file": sample_upload()})
        data = Invoice.objects.get().to_paypal_data()
        self.assertEqual(data["items"][1], {"name": "Logo variations", "quantity": 3, "unit_price": 150.0})
        self.assertEqual(data["due_date"], "2026-11-30")


@override_settings(MEDIA_ROOT="/tmp/billpilot_test_media")
class ReviewTests(TestCase):
    def setUp(self):
        with patch("invoices.pipeline.extract_invoice", return_value=FAKE_RESULT):
            self.client.post(reverse("upload"), {"file": sample_upload()})
        self.invoice = Invoice.objects.get()
        self.url = reverse("invoice_review", args=[self.invoice.pk])

    def payload(self, **overrides):
        items = list(self.invoice.items.all())
        data = {
            "client_email": "new@client.example.com",
            "client_name": "Brightwave Labs",
            "currency": "usd",
            "due_date": "2026-12-15",
            "note": "",
            "items-TOTAL_FORMS": "2",
            "items-INITIAL_FORMS": "2",
            "items-MIN_NUM_FORMS": "1",
            "items-MAX_NUM_FORMS": "1000",
            "items-0-id": items[0].pk, "items-0-invoice": self.invoice.pk,
            "items-0-name": items[0].name, "items-0-quantity": "1", "items-0-unit_price": "1000.00",
            "items-1-id": items[1].pk, "items-1-invoice": self.invoice.pk,
            "items-1-name": items[1].name, "items-1-quantity": "3", "items-1-unit_price": "150.00",
        }
        data.update(overrides)
        return data

    def test_get_shows_prefilled_form(self):
        resp = self.client.get(self.url)
        self.assertContains(resp, "billing@brightwave.example.com")
        self.assertContains(resp, "Brand identity design")

    def test_save_updates_fields_and_items(self):
        resp = self.client.post(self.url, self.payload())
        self.assertRedirects(resp, self.url)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.client_email, "new@client.example.com")
        self.assertEqual(self.invoice.currency, "USD")
        self.assertEqual(str(self.invoice.total), "1450.00")

    def test_delete_item(self):
        data = self.payload(**{"items-1-DELETE": "on"})
        self.client.post(self.url, data)
        self.assertEqual(self.invoice.items.count(), 1)

    def test_add_item(self):
        data = self.payload(**{
            "items-TOTAL_FORMS": "3",
            "items-2-id": "", "items-2-invoice": self.invoice.pk,
            "items-2-name": "Rush fee", "items-2-quantity": "1", "items-2-unit_price": "99.50",
        })
        self.client.post(self.url, data)
        self.assertEqual(self.invoice.items.count(), 3)

    def test_invalid_email_is_rejected(self):
        resp = self.client.post(self.url, self.payload(client_email="not-an-email"))
        self.assertEqual(resp.status_code, 200)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.client_email, "billing@brightwave.example.com")

    def test_negative_price_and_zero_qty_rejected(self):
        resp = self.client.post(self.url, self.payload(**{"items-0-unit_price": "-5", "items-1-quantity": "0"}))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(str(Invoice.objects.get().total), "1650.00")

    def test_cannot_delete_all_items(self):
        data = self.payload(**{"items-0-DELETE": "on", "items-1-DELETE": "on"})
        resp = self.client.post(self.url, data)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.invoice.items.count(), 2)

    def test_sent_invoice_redirects_to_detail(self):
        self.invoice.state = Invoice.State.SENT
        self.invoice.save()
        resp = self.client.get(self.url)
        self.assertRedirects(resp, reverse("invoice_detail", args=[self.invoice.pk]))