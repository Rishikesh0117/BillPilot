"""PDF → text for BillPilot.

1. Native text: pdfplumber reads the text layer (fast, exact) — works for the
   generated samples and most digital invoices.
2. OCR fallback: for scanned PDFs with no text layer, render pages to images and
   run Tesseract. Needs `pip install pytesseract` AND the Tesseract program
   installed on the machine (Windows: UB Mannheim installer; then either add it
   to PATH or set TESSERACT_CMD in .env).

Usage:
    from services.ocr import extract_text
    result = extract_text("samples/invoice_classic_usd.pdf")   # path, or a Django UploadedFile
    result["text"], result["method"], result["pages"]
"""
import os

import pdfplumber

MIN_CHARS_PER_PAGE = 30  # below this we treat the PDF as scanned


class OCRError(Exception):
    pass


def _native_text(pdf):
    return "\n\n".join((page.extract_text() or "").strip() for page in pdf.pages).strip()


def _ocr_text(pdf, resolution=200):
    try:
        import pytesseract
    except ImportError as e:
        raise OCRError(
            "This PDF looks scanned and needs OCR. Install it with: pip install pytesseract "
            "(and install the Tesseract program)."
        ) from e

    cmd = os.getenv("TESSERACT_CMD")
    if cmd:
        pytesseract.pytesseract.tesseract_cmd = cmd

    pages = []
    for page in pdf.pages:
        image = page.to_image(resolution=resolution).original
        try:
            pages.append(pytesseract.image_to_string(image).strip())
        except pytesseract.TesseractNotFoundError as e:
            raise OCRError(
                "Tesseract is not installed or not on PATH. Install it, or set TESSERACT_CMD in .env."
            ) from e
    return "\n\n".join(pages).strip()


def extract_text(source):
    """source: file path or file-like object (e.g. Django UploadedFile).

    Returns {"text": str, "method": "native" | "ocr", "pages": int}.
    Raises OCRError if the file can't be read or contains no text.
    """
    try:
        if hasattr(source, "seek"):
            source.seek(0)
        pdf = pdfplumber.open(source)
    except Exception as e:  # corrupt / not a PDF
        raise OCRError(f"Could not open PDF: {e}") from e

    with pdf:
        pages = len(pdf.pages)
        if pages == 0:
            raise OCRError("PDF has no pages.")

        text = _native_text(pdf)
        method = "native"
        if len(text) < MIN_CHARS_PER_PAGE * pages:
            text = _ocr_text(pdf)
            method = "ocr"

    if not text:
        raise OCRError("No text could be extracted from this PDF.")
    return {"text": text, "method": method, "pages": pages}


if __name__ == "__main__":
    import sys

    for path in sys.argv[1:]:
        r = extract_text(path)
        print(f"=== {path} ({r['method']}, {r['pages']} page(s)) ===")
        print(r["text"], "\n")