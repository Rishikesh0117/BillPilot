"""Generate fake invoice PDFs (4 layouts) and a ground-truth file for testing.

Usage (from repo root):
    pip install reportlab python-dotenv
    python scripts/generate_samples.py

Writes samples/*.pdf and samples/expected.json.
Client emails use PAYER_EMAIL from .env (your US sandbox Personal account) so the
generated invoices can actually be sent and paid in the sandbox. Without it,
placeholder example.com addresses are used.
"""
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

load_dotenv()
OUT = Path(__file__).resolve().parent.parent / "samples"
EMAIL = os.getenv("PAYER_EMAIL")

SAMPLES = [
    {
        "file": "invoice_classic_usd.pdf", "layout": "classic",
        "vendor": "Northwind Design Studio", "invoice_no": "INV-1042",
        "issue_date": "2026-11-01", "due_date": "2026-11-30",
        "client_name": "Brightwave Labs", "client_email": EMAIL or "billing@brightwave.example.com",
        "currency": "USD", "symbol": "$",
        "items": [("Brand identity design", 1, 1200.00), ("Logo variations", 3, 150.00)],
        "note": "Payment due within 30 days.",
    },
    {
        "file": "invoice_minimal_eur.pdf", "layout": "minimal",
        "vendor": "Atelier Lumen", "invoice_no": "2026-077",
        "issue_date": "2026-11-03", "due_date": "2026-12-03",
        "client_name": "Maison Verte", "client_email": EMAIL or "accounts@maisonverte.example.com",
        "currency": "EUR", "symbol": "€",
        "items": [("Photography session", 1, 800.00), ("Photo retouching", 12, 25.00)],
        "note": "Thank you for your business.",
    },
    {
        "file": "invoice_consulting_gbp.pdf", "layout": "classic",
        "vendor": "Harbor Analytics Ltd", "invoice_no": "HA-3319",
        "issue_date": "2026-11-05", "due_date": "2026-11-20",
        "client_name": "Fenwick Retail Group", "client_email": EMAIL or "ap@fenwick.example.com",
        "currency": "GBP", "symbol": "£",
        "items": [("Data audit", 2, 450.00), ("Dashboard setup", 1, 975.50), ("Training workshop", 1, 300.00)],
        "note": "Late payments may incur a 2% monthly fee.",
    },
    {
        "file": "receipt_style_usd.pdf", "layout": "receipt",
        "vendor": "Corner Print & Copy", "invoice_no": "R-88231",
        "issue_date": "2026-11-07", "due_date": None,
        "client_name": "Oakline Events", "client_email": EMAIL or "hello@oakline.example.com",
        "currency": "USD", "symbol": "$",
        "items": [("Poster printing A2", 10, 14.50), ("Banner 2x1m", 2, 39.90)],
        "note": "",
    },
]


def total(s):
    return sum(q * p for _, q, p in s["items"])


def money(s, v):
    return f'{s["symbol"]}{v:,.2f}'


def classic(c, s):
    w, h = A4
    c.setFont("Helvetica-Bold", 20); c.drawString(50, h - 60, s["vendor"])
    c.setFont("Helvetica-Bold", 26); c.drawRightString(w - 50, h - 60, "INVOICE")
    c.setFont("Helvetica", 10)
    c.drawRightString(w - 50, h - 80, f'Invoice #: {s["invoice_no"]}')
    c.drawRightString(w - 50, h - 95, f'Date: {s["issue_date"]}')
    if s["due_date"]:
        c.drawRightString(w - 50, h - 110, f'Due date: {s["due_date"]}')
    c.setFont("Helvetica-Bold", 11); c.drawString(50, h - 150, "Bill To:")
    c.setFont("Helvetica", 11)
    c.drawString(50, h - 167, s["client_name"]); c.drawString(50, h - 182, s["client_email"])
    y = h - 230
    c.setFont("Helvetica-Bold", 10)
    for x, t in ((50, "Description"), (330, "Qty"), (400, "Unit price"), (490, "Amount")):
        c.drawString(x, y, t)
    c.line(50, y - 5, w - 50, y - 5)
    c.setFont("Helvetica", 10); y -= 25
    for name, q, p in s["items"]:
        c.drawString(50, y, name); c.drawString(330, y, str(q))
        c.drawString(400, y, money(s, p)); c.drawString(490, y, money(s, q * p)); y -= 20
    c.line(50, y, w - 50, y)
    c.setFont("Helvetica-Bold", 12)
    c.drawString(400, y - 22, f'Total: {money(s, total(s))} {s["currency"]}')
    if s["note"]:
        c.setFont("Helvetica-Oblique", 10); c.drawString(50, y - 70, s["note"])


def minimal(c, s):
    w, h = A4
    c.setFont("Helvetica-Bold", 16); c.drawCentredString(w / 2, h - 70, s["vendor"])
    c.setFont("Helvetica", 10)
    c.drawCentredString(w / 2, h - 90, f'Invoice {s["invoice_no"]}  |  Issued {s["issue_date"]}  |  Payable by {s["due_date"]}')
    c.setFont("Helvetica", 11)
    c.drawString(80, h - 150, f'Client: {s["client_name"]}')
    c.drawString(80, h - 168, f'Contact: {s["client_email"]}')
    y = h - 220
    for name, q, p in s["items"]:
        c.drawString(80, y, f'{name} - {q} x {money(s, p)}')
        c.drawRightString(w - 80, y, money(s, q * p)); y -= 22
    c.setFont("Helvetica-Bold", 12)
    c.drawRightString(w - 80, y - 15, f'Amount due: {money(s, total(s))} ({s["currency"]})')
    if s["note"]:
        c.setFont("Helvetica-Oblique", 10); c.drawCentredString(w / 2, y - 70, s["note"])


def receipt(c, s):
    w, h = A4
    x = 180
    c.setFont("Courier-Bold", 13); c.drawCentredString(w / 2, h - 80, s["vendor"].upper())
    c.setFont("Courier", 10)
    c.drawCentredString(w / 2, h - 98, f'Order {s["invoice_no"]} - {s["issue_date"]}')
    c.drawString(x, h - 135, f'Customer: {s["client_name"]}')
    c.drawString(x, h - 150, f'Email: {s["client_email"]}')
    c.drawString(x, h - 168, "-" * 38)
    y = h - 185
    for name, q, p in s["items"]:
        c.drawString(x, y, name); y -= 14
        c.drawString(x + 15, y, f'{q} x {money(s, p)}'); c.drawRightString(w - x, y, money(s, q * p)); y -= 20
    c.drawString(x, y, "-" * 38)
    c.setFont("Courier-Bold", 11)
    c.drawString(x, y - 18, "TOTAL"); c.drawRightString(w - x, y - 18, f'{money(s, total(s))} {s["currency"]}')


LAYOUTS = {"classic": classic, "minimal": minimal, "receipt": receipt}


def main():
    OUT.mkdir(exist_ok=True)
    expected = {}
    for s in SAMPLES:
        c = canvas.Canvas(str(OUT / s["file"]), pagesize=A4)
        LAYOUTS[s["layout"]](c, s)
        c.save()
        expected[s["file"]] = {
            "client_email": s["client_email"],
            "client_name": s["client_name"],
            "currency": s["currency"],
            "due_date": s["due_date"],
            "total": round(total(s), 2),
            "items": [{"name": n, "quantity": q, "unit_price": p} for n, q, p in s["items"]],
        }
        print("wrote", s["file"])
    (OUT / "expected.json").write_text(json.dumps(expected, indent=2), encoding="utf-8")
    print("wrote expected.json")


if __name__ == "__main__":
    main()