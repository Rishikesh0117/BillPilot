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

## Part 3 spikes (decided)
- 3A decision: chat model is `openai/gpt-oss-120b` (well-formed `add` tool call on the spike; `google/gemma-3-27b-it` returned content only, no tool call). Set `CHAT_LLM_MODEL=openai/gpt-oss-120b` in .env; when unset it defaults to LLM_MODEL. Extraction stays on LLM_MODEL.
- 3B decision: keep our own `paypal_client.py` for the agent tools. The official toolkit is Node-only and the owner does not want a Node sidecar; our client already covers create/send/status/remind/delete. (Spike script removed.)

## Agent eval (Part 10)
- Harness: `python scripts/eval_agent.py [--repeat N]` runs 18 scenarios in `evals/agent_scenarios.json` against fresh fixtures in a temp DB. PayPal mocked (writes blocked + counted), LLM real, rule-based scoring, results to `evals/results/`.
- Latest full run (`openai/gpt-oss-120b`, Nov 9, `evals/results/agent_eval_openai_gpt_oss_120b_20261109_1103.json`): **49/54 runs passed, 15/18 scenarios 3/3 consistent, 0 unauthorised executions**, ~83k tokens, ~29 min (includes two 90s API timeouts).
- Known failure modes: (1) `invalid-status` 0/3 — the model answers "couldn't find invoice 99" from memory without calling `get_invoice`; two prompt variants tried, no effect, reverted; benign (safe decline, no invented data, nothing executed). (2) Transient Nebius read timeouts (~2/54 runs) fail the affected run; infra flake, not model behaviour.
- Before/after: calibration was 16/18 (plus a harness unicode-print crash, fixed); `multi-followup-pronoun` fixed by rewriting an unfair ambiguous scenario (was "remind the first one", now resolves "the Cedar Works one" via history) — 3/3 since.
- Scope guardrail (Nov 9, uncommitted): system prompt now restricts the assistant to invoices/clients/payments and refuses to write code; new `out-of-scope-code` eval scenario (recursion request) passes, 19 scenarios total.

## Currency conversion (spike, Nov 9, uncommitted)
- PayPal has no usable FX endpoint for us: Currency Exchange v2 (`/v2/pricing/quote-exchange-rates`) is limited-release, approval-gated, built for Orders-checkout pricing — not invoice math.
- Instead: `services/fx.py` converts at live daily rates from the free no-key endpoint `open.er-api.com` (24h in-memory cache); mocked unit tests in `FXTests`, live-checked (800 EUR → 891.94 USD).
- Wired as a read-only `convert_currency` agent tool (live daily rates, 24h cache); the assistant converts instead of asking the user for rates.

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
- [x] Part 3: chat model openai/gpt-oss-120b, keep paypal_client.py (uncommitted)
- [x] Part 4: agent backend with approval-gated proposals (uncommitted)
- [x] Part 5: chat UI with approval cards (uncommitted; server-rendered, no fetch enhancement)
- [x] Part 10: agent eval harness, 18 scenarios, one summary run (49/54, 0 unauthorised)
- [x] Part 11: red-team log (docs/red-team.md, 26 attacks, 0 bypasses open), atomic approve/reject, 18 regression tests
- [ ] Chat layer (optional)
- [ ] Demo video, README, Devpost submission

## Schedule
Nov 6-8 core flow, Nov 9 chat layer, Nov 10 polish, Nov 11 video + README + Devpost, Nov 12 buffer and submit.