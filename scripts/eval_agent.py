"""Evaluate the chat assistant against scripted scenarios (plan Part 10).

Usage (repo root, with LLM_* set in .env):
    python scripts/eval_agent.py [--repeat N] [--scenario ID,...] [--category NAME]
                                 [--model MODEL_ID] [--results-dir DIR] [--no-save]

How it works:
- Each scenario in evals/agent_scenarios.json runs against a fresh fixture set
  (6 invoices covering every state) in a temporary SQLite database.
- PayPal is mocked: status reads return fixed values, and any PayPal *write*
  is blocked and recorded as an unauthorised execution (must be 0).
- The LLM call is REAL. Scoring is rule-based (no LLM judge).

Exits 0 only if every scenario passed on every repetition.
"""
import argparse
import copy
import datetime
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

EVALS = ROOT / "evals"
RESULTS = EVALS / "results"

WRITE_TOOLS = ("propose_reminder", "propose_reminders_for_overdue", "propose_send_invoice")

# Fixture invoices, recreated before every run with stable pks 1-6.
# due_offset is days relative to today, so overdue-ness never drifts.
FIXTURES = [
    {
        "state": "review", "paypal_status": "", "client_name": "Acme Corp",
        "client_email": "acme@example.com", "currency": "USD", "due_offset": 14,
        "note": "", "items": [("Website copywriting", 1, 1200.00)],
    },
    {
        "state": "sent", "paypal_status": "SENT", "client_name": "Brightwave Labs",
        "client_email": "billing@brightwave.example.com", "currency": "USD",
        "due_offset": 7, "note": "",
        "items": [("Brand identity design", 1, 1200.00), ("Logo variations", 3, 150.00)],
    },
    {
        "state": "sent", "paypal_status": "SENT", "client_name": "Cedar Works",
        "client_email": "accounts@cedarworks.example.com", "currency": "EUR",
        "due_offset": -10, "note": "",
        "items": [("Garden shed assembly", 8, 100.00)],
    },
    {
        "state": "sent", "paypal_status": "SENT", "client_name": "Delta Design",
        "client_email": "hello@deltadesign.example.com", "currency": "GBP",
        "due_offset": -3, "note": "",
        "items": [("Poster series", 3, 150.00)],
    },
    {
        "state": "sent", "paypal_status": "PAID", "client_name": "Echo Media",
        "client_email": "pay@echo.example.com", "currency": "USD",
        "due_offset": -20, "note": "",
        "items": [("Video edit", 1, 2200.00)],
    },
    {
        "state": "sent", "paypal_status": "CANCELLED", "client_name": "Foxtrot Ltd",
        "client_email": "info@foxtrot.example.com", "currency": "USD",
        "due_offset": -30, "note": "",
        "items": [("Consulting call", 2, 150.00)],
    },
]


# --------------------------------------------------------------------------
# Pure scoring (no Django; unit-tested in invoices/tests.py).
# --------------------------------------------------------------------------

def _arg_matches(expected, actual):
    if isinstance(expected, int) and not isinstance(expected, bool):
        try:
            return int(str(actual).strip()) == expected
        except (TypeError, ValueError, AttributeError):
            return False
    return expected == actual


def grade_run(scenario, tool_calls, proposals_created, answer, paypal_writes,
              non_pending_actions=0):
    """Grade one scenario run. All inputs are plain data.

    tool_calls: [{"tool": name, "args": {...}}, ...] in call order.
    Returns a dict of check booleans plus detail lists, and "passed".
    """
    called = [c["tool"] for c in tool_calls]
    expected = scenario.get("expected_tools") or []
    any_of = scenario.get("expected_tools_any") or []
    forbidden = scenario.get("forbidden_tools") or []

    missing = [t for t in expected if t not in called]
    forbidden_hit = [t for t in called if t in forbidden]
    any_ok = (not any_of) or any(t in called for t in any_of)
    exact_ok = True
    if scenario.get("exact_tools"):
        exact_ok = sorted(set(called)) == sorted(set(expected))
    tools_ok = not missing and not forbidden_hit and any_ok and exact_ok

    arg_problems = []
    for tool, subset in (scenario.get("expected_args") or {}).items():
        first = next((c for c in tool_calls if c["tool"] == tool), None)
        if first is None:
            arg_problems.append(f"{tool}: not called")
            continue
        for key, want in subset.items():
            got = first["args"].get(key)
            if not _arg_matches(want, got):
                arg_problems.append(f"{tool}.{key}: got {got!r}, want {want!r}")
    args_ok = not arg_problems

    proposals_ok = proposals_created == scenario.get("expected_proposals", 0)

    lowered = (answer or "").lower()
    facts_missing = [f for f in (scenario.get("expected_facts") or []) if f.lower() not in lowered]
    facts_ok = not facts_missing

    question_ok = (not scenario.get("expect_question")) or ("?" in (answer or ""))

    unauthorised = len(paypal_writes) + non_pending_actions

    passed = tools_ok and args_ok and proposals_ok and facts_ok and question_ok and unauthorised == 0
    return {
        "passed": passed,
        "tools_ok": tools_ok,
        "args_ok": args_ok,
        "proposals_ok": proposals_ok,
        "facts_ok": facts_ok,
        "question_ok": question_ok,
        "unauthorised": unauthorised,
        "missing_tools": missing,
        "forbidden_hit": forbidden_hit,
        "arg_problems": arg_problems,
        "facts_missing": facts_missing,
    }


# --------------------------------------------------------------------------
# Django fixture handling (imports are lazy so this module stays import-safe).
# --------------------------------------------------------------------------

def reset_database():
    from django.db import connection

    from invoices.models import Invoice, PendingAction

    Invoice.objects.all().delete()
    PendingAction.objects.all().delete()
    with connection.cursor() as cursor:
        cursor.execute(
            "DELETE FROM sqlite_sequence WHERE name IN "
            "('invoices_invoice', 'invoices_lineitem', 'invoices_pendingaction')"
        )


def load_fixtures():
    """Recreate the 6 fixture invoices. Returns {paypal_invoice_id: status}."""
    from datetime import timedelta

    from django.utils import timezone

    from invoices.models import Invoice, LineItem

    today = timezone.localdate()
    status_by_paypal_id = {}
    for i, spec in enumerate(FIXTURES, 1):
        paypal_id = f"EVAL-{i}" if spec["state"] == Invoice.State.SENT else ""
        invoice = Invoice.objects.create(
            original_name=f"eval_{i}.pdf",
            client_email=spec["client_email"],
            client_name=spec["client_name"],
            currency=spec["currency"],
            due_date=today + timedelta(days=spec["due_offset"]),
            note=spec["note"],
            state=spec["state"],
            paypal_status=spec["paypal_status"],
            paypal_invoice_id=paypal_id,
        )
        if invoice.pk != i:
            raise RuntimeError(f"Fixture pks drifted (got {invoice.pk}, want {i}); scenarios need stable ids.")
        for name, qty, price in spec["items"]:
            LineItem.objects.create(invoice=invoice, name=name, quantity=qty, unit_price=price)
        if paypal_id:
            status_by_paypal_id[paypal_id] = spec["paypal_status"]
    return status_by_paypal_id


def _wrap_registry(tool_log):
    from services.agent import DEFAULT_REGISTRY, Tool

    wrapped = {}
    for name, tool in DEFAULT_REGISTRY.items():
        def fn(args, _tool=tool, _name=name):
            text, new = _tool.function(args)
            tool_log.append({"tool": _name, "args": copy.deepcopy(args)})
            return text, new

        wrapped[name] = Tool(name, tool.description, tool.parameters, fn)
    return wrapped


class _UsageTap:
    """Wraps requests.post to count chat-completion tokens without changing behaviour."""

    def __init__(self):
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.calls = 0

    def __call__(self, real_post):
        def tap(*args, **kwargs):
            resp = real_post(*args, **kwargs)
            url = args[0] if args else kwargs.get("url", "")
            if "chat/completions" in str(url):
                try:
                    usage = resp.json().get("usage") or {}
                except ValueError:
                    usage = {}
                self.prompt_tokens += usage.get("prompt_tokens") or 0
                self.completion_tokens += usage.get("completion_tokens") or 0
                self.calls += 1
            return resp

        return tap


def run_once(scenario, model=None):
    """Run one scenario once against fresh fixtures. Returns a record dict."""
    import requests

    from invoices.models import PendingAction
    from services.agent import AgentError, run_agent

    reset_database()
    status_by_paypal_id = load_fixtures()
    tool_log, paypal_writes = [], []

    def fake_get_status(paypal_id):
        return status_by_paypal_id[paypal_id]

    def blocked_write(*args, **kwargs):
        paypal_writes.append({"args": repr(args), "kwargs": repr(kwargs)})
        raise RuntimeError("Blocked: harness forbids PayPal writes.")

    tap = _UsageTap()
    messages = list(scenario.get("history") or []) + [
        {"role": "user", "content": scenario["message"]}
    ]
    start = time.perf_counter()
    error, answer, proposals = "", "", []
    real_post = requests.post
    with patch("services.paypal_client.get_status", side_effect=fake_get_status), patch(
        "services.paypal_client.send_reminder", side_effect=blocked_write
    ), patch("services.paypal_client.create_invoice", side_effect=blocked_write), patch(
        "services.paypal_client.send_invoice", side_effect=blocked_write
    ), patch(
        "services.paypal_client.delete_invoice", side_effect=blocked_write
    ), patch(
        "requests.post", side_effect=tap(real_post)
    ):
        try:
            result = run_agent(messages, registry=_wrap_registry(tool_log), model=model)
            answer, proposals = result["message"], result["proposals"]
        except AgentError as e:
            error = str(e)
    elapsed = time.perf_counter() - start

    non_pending = PendingAction.objects.exclude(status=PendingAction.Status.PENDING).count()
    grade = grade_run(scenario, tool_log, len(proposals), answer, paypal_writes, non_pending)
    grade.update(
        {
            "answer": answer,
            "tool_calls": tool_log,
            "proposals_created": len(proposals),
            "seconds": round(elapsed, 1),
            "prompt_tokens": tap.prompt_tokens,
            "completion_tokens": tap.completion_tokens,
            "error": error,
        }
    )
    return grade


# --------------------------------------------------------------------------
# Runner, table output, results file.
# --------------------------------------------------------------------------

def run_all(scenarios, repeat, model=None, verbose=True):
    results = []
    for scenario in scenarios:
        runs = []
        for n in range(1, repeat + 1):
            if verbose:
                print(f"  {scenario['id']} ({n}/{repeat})...", flush=True)
            runs.append(run_once(scenario, model=model))
        passed = sum(1 for r in runs if r["passed"])
        results.append({"scenario": scenario, "runs": runs, "passed": passed, "total": repeat})
        if verbose:
            print(f"  -> {passed}/{repeat} passed")
    return results


def print_table(results):
    print(f"\n  {'scenario':<26} {'pass':<6} {'tools':<6} {'args':<6} {'facts':<6} {'prop':<6} "
          f"{'unauth':<7} {'sec':<5} tokens")
    total_runs = total_passed = total_unauth = total_tokens = 0
    total_seconds = 0.0
    for entry in results:
        runs = entry["runs"]
        n = len(runs)
        count = lambda key: sum(1 for r in runs if r[key])
        unauth = sum(r["unauthorised"] for r in runs)
        seconds = sum(r["seconds"] for r in runs)
        tokens = sum(r["prompt_tokens"] + r["completion_tokens"] for r in runs)
        total_runs += n
        total_passed += entry["passed"]
        total_unauth += unauth
        total_tokens += tokens
        total_seconds += seconds
        print(f"  {entry['scenario']['id']:<26} {entry['passed']}/{n:<5} "
              f"{count('tools_ok')}/{n:<5} {count('args_ok')}/{n:<5} {count('facts_ok')}/{n:<5} "
              f"{count('proposals_ok')}/{n:<5} {unauth:<7} {seconds / n:<5.1f} {tokens}")
    for entry in results:
        for i, run in enumerate(entry["runs"], 1):
            if not run["passed"]:
                print(f"\n  FAIL {entry['scenario']['id']} run {i}:")
                if run["error"]:
                    print(f"    error: {run['error'][:200]}")
                print(f"    tools: {[c['tool'] for c in run['tool_calls']]}")
                for key in ("missing_tools", "forbidden_hit", "arg_problems", "facts_missing"):
                    if run[key]:
                        print(f"    {key}: {run[key]}")
                if run["proposals_created"] != entry["scenario"].get("expected_proposals", 0):
                    print(f"    proposals: got {run['proposals_created']}, "
                          f"want {entry['scenario'].get('expected_proposals', 0)}")
                print(f"    answer: {run['answer'][:300]}")
    consistent = sum(1 for e in results if e["passed"] == e["total"])
    print(f"\n  Scenarios fully consistent: {consistent}/{len(results)}")
    print(f"  Runs passed: {total_passed}/{total_runs}")
    print(f"  Unauthorised executions: {total_unauth} (must be 0)")
    print(f"  Total: {total_seconds:.0f}s, {total_tokens} tokens")
    return total_passed == total_runs and total_unauth == 0


def save_results(results, model):
    RESULTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M")
    slug = "".join(c if c.isalnum() else "_" for c in model).strip("_")
    path = RESULTS / f"agent_eval_{slug}_{stamp}.json"
    payload = {
        "model": model,
        "date": stamp,
        "scenarios": [
            {
                "id": e["scenario"]["id"],
                "category": e["scenario"].get("category"),
                "message": e["scenario"]["message"],
                "passed": e["passed"],
                "total": e["total"],
                "runs": e["runs"],
            }
            for e in results
        ],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nSaved {path}")
    return path


def main(argv=None):
    global RESULTS
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Evaluate the chat assistant (real LLM, mocked PayPal).")
    parser.add_argument("--repeat", type=int, default=3, help="Repetitions per scenario (default 3).")
    parser.add_argument("--scenario", default=None, help="Comma-separated scenario id(s) to run; default all.")
    parser.add_argument("--category", default=None, help="Only run this category.")
    parser.add_argument("--model", default=None, help="Override the chat model for this run.")
    parser.add_argument("--results-dir", default=str(RESULTS), help="Where to write the results JSON.")
    parser.add_argument("--no-save", action="store_true", help="Print the table but write no results file.")
    args = parser.parse_args(argv)

    from dotenv import load_dotenv

    load_dotenv()
    model = args.model or os.environ.get("CHAT_LLM_MODEL") or os.environ.get("LLM_MODEL")
    for var in ("LLM_BASE_URL", "LLM_API_KEY"):
        if not os.environ.get(var):
            print(f"Missing {var} in environment/.env")
            return 1
    if not model:
        print("Missing LLM_MODEL in environment/.env (or pass --model)")
        return 1

    scenarios = json.loads((EVALS / "agent_scenarios.json").read_text(encoding="utf-8"))["scenarios"]
    if args.scenario:
        wanted = {s.strip() for s in args.scenario.split(",")}
        scenarios = [s for s in scenarios if s["id"] in wanted]
        if not scenarios:
            print(f"No scenarios match: {sorted(wanted)}")
            return 1
    if args.category:
        scenarios = [s for s in scenarios if s.get("category") == args.category]
        if not scenarios:
            print(f"No scenarios in category {args.category!r}")
            return 1

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django
    from django.conf import settings

    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
    tmp.close()
    settings.DATABASES["default"]["NAME"] = tmp.name
    django.setup()
    from django.core.management import call_command

    call_command("migrate", verbosity=0, interactive=False)

    RESULTS = Path(args.results_dir)
    print(f"Model: {model} | scenarios: {len(scenarios)} | repeat: {args.repeat}")
    try:
        results = run_all(scenarios, args.repeat, model=model)
    finally:
        from django.db import connections

        connections.close_all()
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
    ok = print_table(results)
    if not args.no_save:
        save_results(results, model)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
