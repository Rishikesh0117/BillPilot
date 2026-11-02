import re

from django import forms
from django.forms import inlineformset_factory

from .models import Invoice, LineItem

MAX_UPLOAD_MB = 10


class UploadForm(forms.Form):
    file = forms.FileField(label="Invoice or receipt (PDF)")

    def clean_file(self):
        f = self.cleaned_data["file"]
        if not f.name.lower().endswith(".pdf"):
            raise forms.ValidationError("Please upload a PDF file.")
        if f.size > MAX_UPLOAD_MB * 1024 * 1024:
            raise forms.ValidationError(f"File is too large (max {MAX_UPLOAD_MB} MB).")
        head = f.read(5)
        f.seek(0)
        if head != b"%PDF-":
            raise forms.ValidationError("That file does not look like a valid PDF.")
        return f


class InvoiceForm(forms.ModelForm):
    class Meta:
        model = Invoice
        fields = ["client_email", "client_name", "currency", "due_date", "note"]
        widgets = {
            "due_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "note": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["client_email"].required = True
        self.fields["currency"].help_text = "3-letter code, e.g. USD, EUR, GBP"

    def clean_currency(self):
        value = self.cleaned_data["currency"].strip().upper()
        if not re.fullmatch(r"[A-Z]{3}", value):
            raise forms.ValidationError("Use a 3-letter currency code such as USD.")
        return value


class LineItemForm(forms.ModelForm):
    class Meta:
        model = LineItem
        fields = ["name", "quantity", "unit_price"]
        widgets = {
            "quantity": forms.NumberInput(attrs={"step": "any", "min": "0"}),
            "unit_price": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
        }

    def clean_quantity(self):
        value = self.cleaned_data["quantity"]
        if value is not None and value <= 0:
            raise forms.ValidationError("Quantity must be greater than 0.")
        return value

    def clean_unit_price(self):
        value = self.cleaned_data["unit_price"]
        if value is not None and value < 0:
            raise forms.ValidationError("Price cannot be negative.")
        return value


LineItemFormSet = inlineformset_factory(
    Invoice, LineItem, form=LineItemForm, extra=0, can_delete=True, min_num=1, validate_min=True
)