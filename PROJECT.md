# BillPilot

Agent that turns an uploaded PDF/receipt into a PayPal invoice, sends it, and tracks payment status.
Built for the PayPal AI Hackathon. Submit by the evening of Nov 12 IST (hard cutoff ~1:30 AM IST, Nov 13).

## Flow
1. Upload PDF → OCR → text
2. LLM extracts invoice fields as JSON (schema in `services/paypal_client.py` docstring)
3. User reviews/edits the extracted fields
4. On approval: create + send PayPal sandbox invoice
5. Status page reads invoice status from PayPal (SENT / PAID / ...)
6. Optional (Nov 9): chat layer — "who hasn't paid?", "remind overdue" — risky actions need approval

## Stack
Django (SQLite for now), requests, python-dotenv, LLM with JSON output, open-source OCR library.

## Structure
- `config/` Django settings and urls
- `invoices/` Django app: models, views, templates
- `services/paypal_client.py` create / send / status / remind
- `services/ocr.py` PDF → text
- `services/extraction.py` text → invoice JSON
- `scripts/test_invoice.py` standalone PayPal smoke test
- `samples/` fake sample invoice PDFs for the demo

## PayPal sandbox facts
- App has the **Invoicing** feature enabled (check the token scope contains `services/invoicing`).
- Invoices cannot be sent to recipients in India, even in the sandbox. Use a US sandbox Personal account (or any non-India email) as the client in demos.
- Sandbox balances and invoice status can lag by a few minutes.
- Secrets live in `.env` (never committed). See `.env.example`.

## Decisions
- Scope: core flow first; chat layer only after the core works end to end.
- Write OCR code fresh with open-source libraries; do not reuse CDSL internship code.
- Demo with clean synthetic sample PDFs.

## Status
- [x] Sandbox app, invoicing scope, test invoice created, sent, paid, status read
- [x] Repo scaffold pushed to GitHub
- [x] `services/paypal_client.py` written (untested in-app)
- [ ] OCR
- [ ] Extraction
- [ ] Django upload / review / send views
- [ ] Status page
- [ ] Sample PDFs
- [ ] Chat layer (optional)
- [ ] Demo video, README, Devpost submission

## Schedule
Nov 6-8 core flow, Nov 9 chat layer, Nov 10 polish, Nov 11 video + README + Devpost, Nov 12 buffer and submit.