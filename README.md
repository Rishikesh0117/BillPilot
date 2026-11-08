# BillPilot

Turn a PDF quote, receipt, or invoice into a **PayPal invoice** — then track it and chase payment.

Built for the **PayPal AI Hackathon**. Upload a PDF → text is extracted → an LLM pulls out the invoice fields → you review and fix them → one click creates and sends a real PayPal sandbox invoice → the status page (and a chat assistant) tracks who hasn't paid.

## The problem

Freelancers and small agencies receive work orders, quotes, and receipts as PDFs and lose hours re-typing them into invoices — then more hours chasing payment. BillPilot removes both chores: it converts the document into a sendable invoice in seconds, and follows up on unpaid ones.

## Features

**Core flow**
- Upload a PDF (up to 10 MB); text extracted with `pdfplumber`, with a Tesseract OCR fallback for scanned pages.
- LLM extracts client email/name, currency, due date, note, and line items as strict JSON, with server-side validation and one automatic retry.
- Human review page: edit every field and line item before anything touches PayPal. Mismatches (e.g. items don't add to the document total) surface as warnings, not silent fixes.
- One click creates **and** sends a PayPal sandbox invoice; a half-created draft is cleaned up if sending fails.
- Invoice list/detail with one coherent status (Awaiting review / Sent / Paid / Cancelled / …), an **Overdue** badge, one-click status refresh, payment reminders, and delete for unsent drafts.

**Assistant (chat)**
- `/chat/` answers "Who hasn't paid?", "Which invoices are overdue?", and similar questions from live data.
- It can **propose** reminders (single or batch for all overdue) and sending of awaiting-review invoices — each proposal is an approval card with Approve/Reject. Nothing is ever sent without a human clicking Approve.
- Read tools run automatically; write tools only create proposals. Invoice data is treated as untrusted text (prompt-injection tested), all tool arguments are validated server-side, and every action executes at most once.

## How it works

```
PDF upload ──► OCR (pdfplumber / Tesseract) ──► LLM extraction (strict JSON)
                                                          │
                                                          ▼
PayPal Invoicing API ◄── review & edit ◄── validated fields + warnings
  create / send /         (human in the loop)
  get / remind
        ▲
        └── status refresh ──► list / detail pages, overdue badges
        └── chat assistant ──► read tools (auto) + propose_* tools (approval-gated)
```

PayPal calls used: create invoice, send invoice, get invoice/status, send reminder, delete draft. We use our own thin `requests` client (`services/paypal_client.py`) rather than the official Node-only Agent Toolkit, so the project stays pure Python with no sidecar.

## Setup

Requirements: Python 3.12+, [Tesseract](https://github.com/tesseract-ocr/tesseract) only if you want scanned-PDF support, a PayPal developer account, and an OpenAI-compatible LLM endpoint.

```bash
git clone <this-repo> && cd BillPilot
python -m venv .venv
.venv\Scripts\activate        # Windows; use source .venv/bin/activate on macOS/Linux
pip install -r requirements.txt
cp .env.example .env          # then fill in the values below
python manage.py migrate
python manage.py runserver
```

Optional, only for scanned/image PDFs: `pip install pytesseract` plus the [Tesseract program](https://github.com/tesseract-ocr/tesseract) itself (on Windows use the UB Mannheim installer, then add it to PATH or set `TESSERACT_CMD` in `.env`). Digital PDFs with a text layer need nothing extra.

Open http://127.0.0.1:8000/ — upload one of the PDFs in `samples/` to try the flow.

### Environment variables (`.env`)

| Variable | Purpose |
|---|---|
| `PAYPAL_CLIENT_ID` / `PAYPAL_SECRET` | PayPal sandbox app credentials |
| `PAYER_EMAIL` | Client email for demos (must be **non-India**, see below) |
| `INVOICER_EMAIL` | Sandbox business email shown as the sender (optional) |
| `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` | OpenAI-compatible endpoint for extraction |
| `CHAT_LLM_MODEL` | Chat model (optional, defaults to `LLM_MODEL`) |
| `TESSERACT_CMD` | Full path to `tesseract.exe` if not on PATH (optional) |

### PayPal sandbox setup

1. At [developer.paypal.com](https://developer.paypal.com/), create a sandbox app and copy its Client ID and Secret into `.env`.
2. Enable the **Invoicing** feature on the app. Without it the token lacks invoicing scope and every call returns 403.
3. Create two sandbox accounts: a Business account (the sender) and a US Personal account (the demo client). Put the Personal account's email in `PAYER_EMAIL`.
4. **PayPal cannot send invoices to recipients in India, even in the sandbox.** Always demo with a non-India client email, or sending fails with a clear message.
5. Sandbox balances and statuses can lag a few minutes — use Refresh if a payment doesn't show immediately.

### LLM setup

Any OpenAI-compatible chat endpoint works. Set `LLM_BASE_URL` to the base URL *without* `/chat/completions`, plus `LLM_API_KEY` and the exact `LLM_MODEL` id. The chat assistant needs a model with reliable tool calling (`CHAT_LLM_MODEL`, e.g. `openai/gpt-oss-120b`; models without tool support can't drive the agent loop).

## Running tests and the eval

```bash
python manage.py test invoices        # 86 unit tests; PayPal and LLM calls are mocked
python scripts/eval_extraction.py             # end-to-end OCR+LLM eval on the 9 sample PDFs
python scripts/eval_extraction.py --model <ID>  # eval with a different model
python scripts/generate_samples.py            # rebuild the sample PDFs + expected.json
```

Latest extraction eval (Nov 6): **9/9 documents perfect (100%)**, ~3.7 s per document, 33 s total. Samples cover tax/discount lines, vendor-vs-client email disambiguation, ordinal dates ("5th Nov 2026"), bare `$` symbols, decimal quantities, and a two-page layout with a long description. For private real-document checks without committing the files: put PDFs + `expected.json` in `samples_private/` (gitignored) and run `python scripts/eval_extraction.py --samples-dir samples_private`.

## Project structure

- `config/` — Django settings and URLs
- `invoices/` — the app: `models.py` (`Invoice`, `LineItem`, `PendingAction`), `pipeline.py` (extraction/send/refresh/remind/execute glue), `views.py`, `forms.py`, templates
- `services/paypal_client.py` — create / send / status / remind / delete
- `services/ocr.py` — PDF → text (native + Tesseract fallback)
- `services/extraction.py` — text → invoice JSON via LLM, with validation
- `services/agent.py` — chat agent: tool loop, read tools, approval-gated proposal tools
- `scripts/` — `generate_samples.py`, `eval_extraction.py`, `test_invoice.py` (PayPal smoke test), `spike_tool_calling.py`
- `samples/` — 9 synthetic demo PDFs + `expected.json` ground truth

## Limitations (honest)

- **Sandbox only** — no production PayPal credentials or live money movement.
- **Tax, discount, subtotal, and shipping lines are never imported as items.** They stay out of the PayPal invoice, and the review page warns when the items don't add to the document total so the reviewer can adjust.
- **Scanned/image PDFs need Tesseract** installed separately; without it only digital (text-layer) PDFs work.
- Currency defaults to USD with a warning when the document gives no recognisable currency.
- The chat assistant proposes; a human must approve every send. Batch reminders are capped at 10 per request.
