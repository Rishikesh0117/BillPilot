from decimal import Decimal

from django.db import models
from django.utils import timezone


FINAL_STATUSES = frozenset({"PAID", "MARKED_AS_PAID", "CANCELLED", "REFUNDED", "MARKED_AS_REFUNDED"})


class Invoice(models.Model):
    class State(models.TextChoices):
        REVIEW = "review", "Awaiting review"
        SENT = "sent", "Sent"
        FAILED = "failed", "Send failed"

    source_file = models.FileField(upload_to="uploads/")
    original_name = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    state = models.CharField(max_length=10, choices=State.choices, default=State.REVIEW)

    # Extracted (and later user-edited) fields
    client_email = models.EmailField(blank=True)
    client_name = models.CharField(max_length=200, blank=True)
    currency = models.CharField(max_length=3, default="USD")
    due_date = models.DateField(null=True, blank=True)
    note = models.TextField(blank=True)

    ocr_method = models.CharField(max_length=10, blank=True)
    warnings = models.JSONField(default=list, blank=True)

    # PayPal side
    paypal_invoice_id = models.CharField(max_length=64, blank=True)
    payer_url = models.URLField(max_length=500, blank=True)
    paypal_status = models.CharField(max_length=20, blank=True)
    error_message = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Invoice #{self.pk} ({self.client_email or 'no client'})"

    @property
    def display_status(self):
        """One coherent status for the list/detail pages, from state + PayPal status."""
        if self.state == self.State.REVIEW:
            return "Awaiting review"
        if self.state == self.State.FAILED:
            return "Send failed"
        return {
            "PAID": "Paid",
            "MARKED_AS_PAID": "Paid",
            "CANCELLED": "Cancelled",
            "REFUNDED": "Refunded",
            "MARKED_AS_REFUNDED": "Refunded",
            "PARTIALLY_PAID": "Partially paid",
            "PARTIALLY_REFUNDED": "Partially paid",
        }.get(self.paypal_status, "Sent")

    @property
    def display_status_key(self):
        """CSS-safe key for the display_status badge."""
        return {
            "Awaiting review": "review",
            "Send failed": "failed",
            "Paid": "paid",
            "Cancelled": "cancelled",
            "Refunded": "refunded",
            "Partially paid": "partial",
        }.get(self.display_status, "sent")

    @property
    def is_overdue(self):
        """Sent, still unpaid, and past its due date."""
        if self.state != self.State.SENT or not self.due_date:
            return False
        if self.paypal_status in FINAL_STATUSES:
            return False
        return self.due_date < timezone.localdate()

    @property
    def total(self):
        return sum((item.amount for item in self.items.all()), Decimal("0"))

    def to_paypal_data(self):
        """Shape expected by services.paypal_client.create_invoice."""

        def clean_qty(q):
            return int(q) if q == q.to_integral_value() else float(q)

        return {
            "client_email": self.client_email,
            "client_name": self.client_name or None,
            "currency": self.currency,
            "due_date": self.due_date.isoformat() if self.due_date else None,
            "note": self.note or None,
            "items": [
                {"name": i.name, "quantity": clean_qty(i.quantity), "unit_price": float(i.unit_price)}
                for i in self.items.all()
            ],
        }


class LineItem(models.Model):
    invoice = models.ForeignKey(Invoice, related_name="items", on_delete=models.CASCADE)
    name = models.CharField(max_length=200)
    quantity = models.DecimalField(max_digits=10, decimal_places=2, default=1)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        ordering = ["id"]

    @property
    def amount(self):
        return (self.quantity * self.unit_price).quantize(Decimal("0.01"))

    def __str__(self):
        return self.name