from django.contrib import messages
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from services.extraction import ExtractionError
from services.ocr import OCRError

from .forms import InvoiceForm, LineItemFormSet, UploadForm
from .models import Invoice
from .pipeline import (
    REMINDABLE_STATUSES,
    refresh_open_invoices,
    refresh_status,
    run_extraction,
    send_invoice_to_paypal,
    send_payment_reminder,
)


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
    can_remind = invoice.paypal_status in REMINDABLE_STATUSES
    return render(request, "invoices/detail.html", {"invoice": invoice, "can_remind": can_remind})


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


@require_POST
def invoice_refresh(request, pk):
    invoice = get_object_or_404(Invoice, pk=pk)
    ok, result = refresh_status(invoice)
    if ok:
        messages.success(request, f"PayPal status: {result}")
    else:
        messages.error(request, result)
    return redirect("invoice_detail", pk=pk)


@require_POST
def invoice_refresh_all(request):
    checked, errors = refresh_open_invoices()
    if checked:
        messages.success(request, f"Checked {checked} open invoice(s) with PayPal.")
    elif not errors:
        messages.success(request, "No open invoices to check.")
    for err in errors:
        messages.error(request, err)
    return redirect("invoice_list")


@require_POST
def invoice_remind(request, pk):
    invoice = get_object_or_404(Invoice, pk=pk)
    note = request.POST.get("note", "").strip()[:300] or None
    ok, message = send_payment_reminder(invoice, note)
    (messages.success if ok else messages.error)(request, message)
    return redirect("invoice_detail", pk=pk)