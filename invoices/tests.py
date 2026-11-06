from datetime import date, timedelta
from pathlib import Path
import json
from unittest.mock import patch

import requests

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from services.extraction import ExtractionError, validate
from services.paypal_client import PayPalError

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


INDIA_BODY = json.dumps({"name": "MEDIA_TYPE_NOT_ACCEPTABLE", "message": "x",
                         "details": [{"issue": "INR_FOREIGN_CURRENCY_BLOCKED"}]})


@override_settings(MEDIA_ROOT="/tmp/billpilot_test_media")
class SendTests(TestCase):
    setUp = ReviewTests.setUp
    payload = ReviewTests.payload

    def send(self, **extra):
        data = self.payload(**extra)
        data["action"] = "send"
        return self.client.post(self.url, data, follow=True)

    @patch("invoices.pipeline.paypal_client")
    def test_send_success(self, pp):
        pp.create_invoice.return_value = "INV2-ABC"
        pp.send_invoice.return_value = "https://sandbox.paypal.com/invoice/p/#INV2-ABC"
        resp = self.send()
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.state, Invoice.State.SENT)
        self.assertEqual(self.invoice.paypal_invoice_id, "INV2-ABC")
        self.assertEqual(self.invoice.payer_url, "https://sandbox.paypal.com/invoice/p/#INV2-ABC")
        self.assertContains(resp, "Invoice sent to PayPal")
        self.assertContains(resp, "View invoice as the customer")

    @patch("invoices.pipeline.paypal_client")
    def test_edits_are_saved_before_sending(self, pp):
        pp.create_invoice.return_value = "INV2-ABC"
        pp.send_invoice.return_value = ""
        self.send()
        sent_data = pp.create_invoice.call_args[0][0]
        self.assertEqual(sent_data["client_email"], "new@client.example.com")
        self.assertEqual(sent_data["items"][0]["unit_price"], 1000.0)

    @patch("invoices.pipeline.paypal_client")
    def test_india_error_is_friendly_and_draft_deleted(self, pp):
        pp.create_invoice.return_value = "INV2-DRAFT"
        pp.send_invoice.side_effect = PayPalError("send", 406, INDIA_BODY)
        resp = self.send()
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.state, Invoice.State.REVIEW)
        self.assertEqual(self.invoice.paypal_invoice_id, "")
        pp.delete_invoice.assert_called_once_with("INV2-DRAFT")
        self.assertContains(resp, "customers in India")

    @patch("invoices.pipeline.paypal_client")
    def test_network_error(self, pp):
        pp.create_invoice.side_effect = requests.ConnectionError()
        resp = self.send()
        self.assertContains(resp, "Could not reach PayPal")
        pp.delete_invoice.assert_not_called()
        self.assertEqual(Invoice.objects.get().state, Invoice.State.REVIEW)

    @patch("invoices.pipeline.paypal_client")
    def test_missing_credentials(self, pp):
        pp.create_invoice.side_effect = KeyError("PAYPAL_CLIENT_ID")
        resp = self.send()
        self.assertContains(resp, "credentials are missing")

    @patch("invoices.pipeline.paypal_client")
    def test_save_does_not_call_paypal(self, pp):
        data = self.payload()
        data["action"] = "save"
        self.client.post(self.url, data)
        pp.create_invoice.assert_not_called()

    @patch("invoices.pipeline.paypal_client")
    def test_cannot_send_twice(self, pp):
        pp.create_invoice.return_value = "INV2-ABC"
        pp.send_invoice.return_value = ""
        self.send()
        self.send()
        self.assertEqual(pp.create_invoice.call_count, 1)


@override_settings(MEDIA_ROOT="/tmp/billpilot_test_media")
class StatusRefreshTests(TestCase):
    def make_sent(self, paypal_id="INV2-A", status="SENT"):
        return Invoice.objects.create(
            original_name="x.pdf", client_email="c@example.com", state=Invoice.State.SENT,
            paypal_invoice_id=paypal_id, paypal_status=status,
        )

    @patch("invoices.pipeline.paypal_client")
    def test_refresh_updates_status(self, pp):
        inv = self.make_sent()
        pp.get_status.return_value = "PAID"
        resp = self.client.post(reverse("invoice_refresh", args=[inv.pk]), follow=True)
        inv.refresh_from_db()
        self.assertEqual(inv.paypal_status, "PAID")
        self.assertContains(resp, "PayPal status: PAID")

    @patch("invoices.pipeline.paypal_client")
    def test_refresh_error_shows_message(self, pp):
        inv = self.make_sent()
        pp.get_status.side_effect = PayPalError("get", 404, "{}")
        resp = self.client.post(reverse("invoice_refresh", args=[inv.pk]), follow=True)
        self.assertContains(resp, "HTTP 404")
        inv.refresh_from_db()
        self.assertEqual(inv.paypal_status, "SENT")

    @patch("invoices.pipeline.paypal_client")
    def test_unsent_invoice_cannot_refresh(self, pp):
        inv = Invoice.objects.create(original_name="x.pdf")
        resp = self.client.post(reverse("invoice_refresh", args=[inv.pk]), follow=True)
        self.assertContains(resp, "hasn&#x27;t been sent")
        pp.get_status.assert_not_called()

    @patch("invoices.pipeline.paypal_client")
    def test_refresh_all_skips_final_and_updates_open(self, pp):
        open_inv = self.make_sent("INV2-OPEN", "SENT")
        self.make_sent("INV2-DONE", "PAID")
        Invoice.objects.create(original_name="draft.pdf")
        pp.get_status.return_value = "PAID"
        resp = self.client.post(reverse("invoice_refresh_all"), follow=True)
        open_inv.refresh_from_db()
        self.assertEqual(open_inv.paypal_status, "PAID")
        pp.get_status.assert_called_once_with("INV2-OPEN")
        self.assertContains(resp, "Checked 1 open invoice")

    @patch("invoices.pipeline.paypal_client")
    def test_refresh_all_stops_on_connection_problem(self, pp):
        self.make_sent("INV2-1")
        self.make_sent("INV2-2")
        pp.get_status.side_effect = requests.ConnectionError()
        resp = self.client.post(reverse("invoice_refresh_all"), follow=True)
        self.assertEqual(pp.get_status.call_count, 1)
        self.assertContains(resp, "Could not reach PayPal")

    def test_refresh_requires_post(self):
        inv = self.make_sent()
        self.assertEqual(self.client.get(reverse("invoice_refresh", args=[inv.pk])).status_code, 405)
        self.assertEqual(self.client.get(reverse("invoice_refresh_all")).status_code, 405)

    def test_list_and_detail_render_status_badges(self):
        inv = self.make_sent(status="PAID")
        self.assertContains(self.client.get(reverse("invoice_list")), "badge paid")
        self.assertContains(self.client.get(reverse("invoice_detail", args=[inv.pk])), ">Refresh status<")


@override_settings(MEDIA_ROOT="/tmp/billpilot_test_media")
class ReminderTests(TestCase):
    def make_sent(self, status="SENT"):
        return Invoice.objects.create(
            original_name="x.pdf", client_email="c@example.com", state=Invoice.State.SENT,
            paypal_invoice_id="INV2-A", paypal_status=status,
        )

    def remind(self, inv, **data):
        return self.client.post(reverse("invoice_remind", args=[inv.pk]), data, follow=True)

    @patch("invoices.pipeline.paypal_client")
    def test_reminder_sent_for_unpaid(self, pp):
        inv = self.make_sent()
        pp.get_status.return_value = "SENT"
        resp = self.remind(inv)
        pp.send_reminder.assert_called_once_with("INV2-A")
        self.assertContains(resp, "Reminder sent to the customer")

    @patch("invoices.pipeline.paypal_client")
    def test_custom_note_is_passed_through(self, pp):
        inv = self.make_sent()
        pp.get_status.return_value = "SENT"
        self.remind(inv, note="Due Friday, thanks!")
        pp.send_reminder.assert_called_once_with("INV2-A", note="Due Friday, thanks!")

    @patch("invoices.pipeline.paypal_client")
    def test_no_reminder_if_already_paid(self, pp):
        inv = self.make_sent()
        pp.get_status.return_value = "PAID"
        resp = self.remind(inv)
        pp.send_reminder.assert_not_called()
        self.assertContains(resp, "No reminder sent: this invoice is PAID")
        inv.refresh_from_db()
        self.assertEqual(inv.paypal_status, "PAID")

    @patch("invoices.pipeline.paypal_client")
    def test_paypal_error_is_shown(self, pp):
        inv = self.make_sent()
        pp.get_status.return_value = "SENT"
        pp.send_reminder.side_effect = PayPalError("remind", 422, json.dumps({"message": "Too many reminders", "details": [{"issue": "REMINDER_LIMIT"}]}))
        resp = self.remind(inv)
        self.assertContains(resp, "Too many reminders")

    @patch("invoices.pipeline.paypal_client")
    def test_unsent_invoice_cannot_be_reminded(self, pp):
        inv = Invoice.objects.create(original_name="x.pdf")
        resp = self.remind(inv)
        self.assertContains(resp, "hasn&#x27;t been sent")
        pp.send_reminder.assert_not_called()

    def test_button_only_shown_when_unpaid(self):
        unpaid, paid = self.make_sent("SENT"), self.make_sent("PAID")
        self.assertContains(self.client.get(reverse("invoice_detail", args=[unpaid.pk])), "Send payment reminder")
        self.assertNotContains(self.client.get(reverse("invoice_detail", args=[paid.pk])), "Send payment reminder")

    def test_reminder_requires_post(self):
        inv = self.make_sent()
        self.assertEqual(self.client.get(reverse("invoice_remind", args=[inv.pk])).status_code, 405)


@override_settings(MEDIA_ROOT="/tmp/billpilot_test_media")
class DisplayStatusTests(TestCase):
    def make(self, state=Invoice.State.REVIEW, paypal_status="", due_date=None):
        return Invoice.objects.create(
            original_name="x.pdf", client_email="c@example.com",
            state=state, paypal_status=paypal_status, due_date=due_date,
        )

    def test_review_state(self):
        self.assertEqual(self.make().display_status, "Awaiting review")

    def test_failed_state(self):
        self.assertEqual(self.make(state=Invoice.State.FAILED).display_status, "Send failed")

    def test_sent_maps_paypal_status(self):
        cases = {
            "SENT": "Sent", "UNPAID": "Sent", "": "Sent",
            "PAID": "Paid", "MARKED_AS_PAID": "Paid",
            "CANCELLED": "Cancelled",
            "REFUNDED": "Refunded", "MARKED_AS_REFUNDED": "Refunded",
            "PARTIALLY_PAID": "Partially paid",
        }
        for paypal_status, expected in cases.items():
            with self.subTest(paypal_status=paypal_status):
                inv = self.make(state=Invoice.State.SENT, paypal_status=paypal_status)
                self.assertEqual(inv.display_status, expected)

    def test_overdue_only_when_sent_unpaid_and_past_due(self):
        past = date.today() - timedelta(days=1)
        future = date.today() + timedelta(days=1)
        self.assertTrue(self.make(state=Invoice.State.SENT, paypal_status="SENT", due_date=past).is_overdue)
        self.assertFalse(self.make(state=Invoice.State.SENT, paypal_status="PAID", due_date=past).is_overdue)
        self.assertFalse(self.make(state=Invoice.State.SENT, paypal_status="CANCELLED", due_date=past).is_overdue)
        self.assertFalse(self.make(due_date=past).is_overdue)
        self.assertFalse(self.make(state=Invoice.State.SENT, paypal_status="SENT", due_date=future).is_overdue)
        self.assertFalse(self.make(state=Invoice.State.SENT, paypal_status="SENT").is_overdue)

    def test_list_paid_invoice_does_not_say_sent(self):
        self.make(state=Invoice.State.SENT, paypal_status="PAID")
        resp = self.client.get(reverse("invoice_list"))
        self.assertContains(resp, ">Paid<")
        self.assertNotContains(resp, ">Sent<")

    def test_list_shows_overdue_badge(self):
        self.make(state=Invoice.State.SENT, paypal_status="SENT",
                  due_date=date.today() - timedelta(days=1))
        self.assertContains(self.client.get(reverse("invoice_list")), ">Overdue<")

    def test_detail_shows_paid_status(self):
        inv = self.make(state=Invoice.State.SENT, paypal_status="PAID")
        resp = self.client.get(reverse("invoice_detail", args=[inv.pk]))
        self.assertContains(resp, ">Paid<")


@override_settings(MEDIA_ROOT="/tmp/billpilot_test_media")
class DeleteDraftTests(TestCase):
    def test_delete_review_invoice(self):
        inv = Invoice.objects.create(original_name="x.pdf")
        resp = self.client.post(reverse("invoice_delete", args=[inv.pk]))
        self.assertRedirects(resp, reverse("invoice_list"))
        self.assertEqual(Invoice.objects.count(), 0)

    def test_cannot_delete_sent_invoice(self):
        inv = Invoice.objects.create(
            original_name="x.pdf", state=Invoice.State.SENT,
            paypal_invoice_id="INV2-A", paypal_status="SENT",
        )
        resp = self.client.post(reverse("invoice_delete", args=[inv.pk]), follow=True)
        self.assertEqual(Invoice.objects.count(), 1)
        self.assertContains(resp, "Only invoices awaiting review can be deleted.")

    def test_delete_requires_post(self):
        inv = Invoice.objects.create(original_name="x.pdf")
        self.assertEqual(self.client.get(reverse("invoice_delete", args=[inv.pk])).status_code, 405)
        self.assertEqual(Invoice.objects.count(), 1)

    def test_delete_button_only_for_review(self):
        review = Invoice.objects.create(original_name="r.pdf")
        sent = Invoice.objects.create(
            original_name="s.pdf", state=Invoice.State.SENT,
            paypal_invoice_id="INV2-A", paypal_status="SENT",
        )
        self.assertContains(self.client.get(reverse("invoice_detail", args=[review.pk])), "Delete draft")
        self.assertNotContains(self.client.get(reverse("invoice_detail", args=[sent.pk])), "Delete draft")


class ExtractionValidateTests(TestCase):
    def raw(self, **overrides):
        payload = {
            "client_email": "billing@client.example.com",
            "client_name": "Client Co",
            "currency": "USD",
            "due_date": "2026-11-30",
            "note": None,
            "items": [{"name": "Design", "quantity": 1, "unit_price": 100.0}],
            "total": 100.0,
        }
        payload.update(overrides)
        return payload

    def test_currency_symbol_maps_to_code(self):
        for symbol, code in (("$", "USD"), ("€", "EUR"), ("£", "GBP")):
            with self.subTest(symbol=symbol):
                data, warnings = validate(self.raw(currency=symbol))
                self.assertEqual(data["currency"], code)
                self.assertEqual(warnings, [])

    def test_currency_name_maps_to_code(self):
        for name, code in (("euro", "EUR"), ("Dollars", "USD"), ("pounds", "GBP")):
            with self.subTest(name=name):
                data, _ = validate(self.raw(currency=name))
                self.assertEqual(data["currency"], code)

    def test_currency_code_passes_through(self):
        data, warnings = validate(self.raw(currency="usd"))
        self.assertEqual(data["currency"], "USD")
        self.assertEqual(warnings, [])

    def test_unknown_currency_defaults_to_usd_with_warning(self):
        for value in ("???", "", None):
            with self.subTest(value=value):
                data, warnings = validate(self.raw(currency=value))
                self.assertEqual(data["currency"], "USD")
                self.assertTrue(any("defaulted to USD" in w for w in warnings))

    def test_total_mismatch_keeps_items_and_warns(self):
        # Tax/discount docs: items stay as extracted, the mismatch is flagged.
        data, warnings = validate(self.raw(
            items=[
                {"name": "Paper", "quantity": 5, "unit_price": 24.0},
                {"name": "Ink", "quantity": 4, "unit_price": 32.5},
            ],
            total=243.0,
        ))
        self.assertEqual(len(data["items"]), 2)
        self.assertTrue(any("add up to" in w for w in warnings))