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
- Tax/discount/subtotal lines are never imported as items (prompt rule); the "items do not add up to the document total" warning flags the gap for the reviewer.

## Extraction eval
- `python scripts/generate_samples.py` rebuilds the 9 sample PDFs + `samples/expected.json`.
- `python scripts/eval_extraction.py [--model ID]` scores all samples; exits non-zero unless every document is perfect.
- Latest live run (owner machine, Nov 6, default LLM_MODEL): 9/9 perfect (100%), 3.7s per document, 33s total. No failures, so no prompt/validate fixes were needed beyond the preventive ones already in.
- Private real-document check: put PDFs + expected.json in `samples_private/` (gitignored, never commit) and run `python scripts/eval_extraction.py --samples-dir samples_private`.

## Status
- [x] Sandbox app, invoicing scope, test invoice created, sent, paid, status read
- [x] Repo scaffold pushed to GitHub
- [x] `services/paypal_client.py` written (untested in-app)
- [x] OCR
- [x] Extraction
- [x] Django upload / review / send views
- [x] Status page
- [x] Sample PDFs
- [x] Part 1 polish: unified status, overdue badge, delete drafts, friendly copy (uncommitted)
- [x] Part 2: 5 harder samples, eval upgrades, currency normalization (uncommitted; live eval 9/9 on owner machine)
- [ ] Chat layer (optional)
- [ ] Demo video, README, Devpost submission

## Schedule
Nov 6-8 core flow, Nov 9 chat layer, Nov 10 polish, Nov 11 video + README + Devpost, Nov 12 buffer and submit.