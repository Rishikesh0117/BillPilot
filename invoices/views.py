from django.contrib import messages
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from services.agent import AgentError, run_agent
from services.extraction import ExtractionError
from services.ocr import OCRError
from services.paypal_client import DEFAULT_REMINDER_NOTE

from .forms import InvoiceForm, LineItemFormSet, UploadForm
from .models import Invoice, PendingAction
from .pipeline import (
    REMINDABLE_STATUSES,
    execute_action,
    refresh_open_invoices,
    refresh_status,
    run_extraction,
    send_invoice_to_paypal,
    send_payment_reminder,
)

CHAT_HISTORY_LIMIT = 20
CHAT_MESSAGE_LIMIT = 1000


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
def invoice_delete(request, pk):
    invoice = get_object_or_404(Invoice, pk=pk)
    if invoice.state != Invoice.State.REVIEW:
        messages.error(request, "Only invoices awaiting review can be deleted.")
        return redirect("invoice_detail", pk=pk)
    invoice.source_file.delete(save=False)
    invoice.delete()
    messages.success(request, f"Draft invoice #{pk} deleted.")
    return redirect("invoice_list")


@require_POST
def invoice_remind(request, pk):
    invoice = get_object_or_404(Invoice, pk=pk)
    note = request.POST.get("note", "").strip()[:300] or None
    ok, message = send_payment_reminder(invoice, note)
    (messages.success if ok else messages.error)(request, message)
    return redirect("invoice_detail", pk=pk)


def _chat_history(request):
    history = request.session.get("chat_history", [])
    return [
        m for m in history
        if m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str)
    ][:CHAT_HISTORY_LIMIT]


def chat(request):
    if request.method == "POST":
        message = (request.POST.get("message") or "").strip()[:CHAT_MESSAGE_LIMIT]
        if not message:
            messages.error(request, "Please type a message.")
            return redirect("chat")
        history = _chat_history(request)
        try:
            result = run_agent(history + [{"role": "user", "content": message}])
        except AgentError as e:
            messages.error(request, f"Assistant error: {e}")
            return redirect("chat")
        reply = result["message"] or (
            "I couldn't come up with a reply just now. Could you rephrase that?"
        )
        request.session["chat_history"] = (history + [
            {"role": "user", "content": message},
            {"role": "assistant", "content": reply},
        ])[-CHAT_HISTORY_LIMIT:]
        if result["proposals"]:
            messages.success(request, f"{len(result['proposals'])} proposal(s) created — review below.")
        return redirect("chat")

    pending = (
        PendingAction.objects.filter(status=PendingAction.Status.PENDING)
        .select_related("invoice")
        .order_by("-created_at")
    )
    last_decision = None
    if "last_decision" in request.session:
        last_id = request.session.pop("last_decision")
        request.session.modified = True
        last_decision = PendingAction.objects.filter(pk=last_id).select_related("invoice").first()
    return render(
        request,
        "invoices/chat.html",
        {
            "history": _chat_history(request),
            "pending": pending,
            "last_decision": last_decision,
            "default_reminder_note": DEFAULT_REMINDER_NOTE,
        },
    )


@require_POST
def action_approve(request, pk):
    action = get_object_or_404(PendingAction.objects.select_related("invoice"), pk=pk)
    # Atomic claim: two concurrent approves (double-click) must not both execute.
    claimed = PendingAction.objects.filter(
        pk=action.pk, status=PendingAction.Status.PENDING
    ).update(status=PendingAction.Status.APPROVED, decided_at=timezone.now())
    if not claimed:
        messages.error(request, "This proposal was already handled.")
        return redirect("chat")
    action.refresh_from_db()
    ok, message = execute_action(action)
    (messages.success if ok else messages.error)(request, message)
    request.session["last_decision"] = action.pk
    return redirect("chat")


@require_POST
def action_reject(request, pk):
    action = get_object_or_404(PendingAction.objects.select_related("invoice"), pk=pk)
    claimed = PendingAction.objects.filter(
        pk=action.pk, status=PendingAction.Status.PENDING
    ).update(status=PendingAction.Status.REJECTED, decided_at=timezone.now())
    if not claimed:
        messages.error(request, "This proposal was already handled.")
        return redirect("chat")
    messages.success(request, "Proposal rejected. Nothing was sent.")
    request.session["last_decision"] = action.pk
    return redirect("chat")
