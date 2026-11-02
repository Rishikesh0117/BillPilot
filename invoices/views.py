from django.contrib import messages
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render

from services.extraction import ExtractionError
from services.ocr import OCRError

from .forms import InvoiceForm, LineItemFormSet, UploadForm
from .models import Invoice
from .pipeline import run_extraction, send_invoice_to_paypal


def upload(request):
    form = UploadForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        upload_file = form.cleaned_data["file"]
        invoice = Invoice.objects.create(source_file=upload_file, original_name=upload_file.name)
        try:
            run_extraction(invoice)
        except (OCRError, ExtractionError) as e:
            invoice.source_file.delete(save=False)
            invoice.delete()
            form.add_error(None, f"Could not read this document: {e}")
        else:
            return redirect("invoice_review", pk=invoice.pk)
    recent = Invoice.objects.all()[:5]
    return render(request, "invoices/upload.html", {"form": form, "recent": recent})


def invoice_list(request):
    return render(request, "invoices/list.html", {"invoices": Invoice.objects.prefetch_related("items")})


def invoice_detail(request, pk):
    invoice = get_object_or_404(Invoice.objects.prefetch_related("items"), pk=pk)
    return render(request, "invoices/detail.html", {"invoice": invoice})


def invoice_review(request, pk):
    invoice = get_object_or_404(Invoice, pk=pk)
    if invoice.state != Invoice.State.REVIEW:
        return redirect("invoice_detail", pk=pk)

    form = InvoiceForm(request.POST or None, instance=invoice)
    formset = LineItemFormSet(request.POST or None, instance=invoice)
    if request.method == "POST" and form.is_valid() and formset.is_valid():
        with transaction.atomic():
            form.save()
            formset.save()
        if request.POST.get("action") == "send":
            if send_invoice_to_paypal(invoice):
                messages.success(request, "Invoice sent to PayPal.")
                return redirect("invoice_detail", pk=pk)
            messages.error(request, invoice.error_message)
            return redirect("invoice_review", pk=pk)
        messages.success(request, "Changes saved.")
        return redirect("invoice_review", pk=pk)
    return render(request, "invoices/review.html", {"invoice": invoice, "form": form, "formset": formset})