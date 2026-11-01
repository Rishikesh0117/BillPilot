from django.shortcuts import get_object_or_404, render, redirect

from services.extraction import ExtractionError
from services.ocr import OCRError

from .forms import UploadForm
from .models import Invoice
from .pipeline import run_extraction


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
            return redirect("invoice_detail", pk=invoice.pk)
    recent = Invoice.objects.all()[:5]
    return render(request, "invoices/upload.html", {"form": form, "recent": recent})


def invoice_list(request):
    return render(request, "invoices/list.html", {"invoices": Invoice.objects.prefetch_related("items")})


def invoice_detail(request, pk):
    invoice = get_object_or_404(Invoice.objects.prefetch_related("items"), pk=pk)
    return render(request, "invoices/detail.html", {"invoice": invoice})