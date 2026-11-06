"""Run OCR + LLM extraction on every sample PDF and score it against expected.json.

Usage (repo root, with LLM_* set in .env):
    python scripts/eval_extraction.py [--model MODEL_ID] [--samples-dir DIR]

--model overrides LLM_MODEL for this run. --samples-dir points at a directory
containing sample PDFs plus expected.json (useful for private real-document
checks; keep such directories out of git).

Exits 0 only if every document extracts perfectly.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from services.extraction import ExtractionError, extract_invoice  # noqa: E402
from services.ocr import extract_text  # noqa: E402

SAMPLES = ROOT / "samples"


def compare(got, exp):
    """Return a list of mismatch descriptions (empty = perfect)."""
    problems = []
    for field in ("client_email", "client_name", "currency", "due_date"):
        if got.get(field) != exp.get(field):
            problems.append(f"{field}: got {got.get(field)!r}, expected {exp.get(field)!r}")

    if len(got["items"]) != len(exp["items"]):
        problems.append(f"item count: got {len(got['items'])}, expected {len(exp['items'])}")
    else:
        for i, (g, e) in enumerate(zip(got["items"], exp["items"]), 1):
            if g["name"].lower() != e["name"].lower():
                problems.append(f"item {i} name: got {g['name']!r}, expected {e['name']!r}")
            if abs(g["quantity"] - e["quantity"]) > 1e-9:
                problems.append(f"item {i} quantity: got {g['quantity']}, expected {e['quantity']}")
            if abs(g["unit_price"] - e["unit_price"]) > 0.01:
                problems.append(f"item {i} unit price: got {g['unit_price']}, expected {e['unit_price']}")
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(description="Score LLM extraction against expected.json.")
    parser.add_argument("--model", default=None, help="Override LLM_MODEL for this run.")
    parser.add_argument("--samples-dir", default=str(SAMPLES),
                        help="Directory with sample PDFs plus expected.json.")
    args = parser.parse_args(argv)
    if args.model:
        os.environ["LLM_MODEL"] = args.model

    samples = Path(args.samples_dir)
    expected = json.loads((samples / "expected.json").read_text(encoding="utf-8"))
    if not expected:
        print("No documents listed in expected.json.")
        return 1

    rows = []  # (filename, result, n_problems, seconds)
    for filename, exp in expected.items():
        print(f"\n{filename}")
        start = time.perf_counter()
        try:
            text = extract_text(str(samples / filename))["text"]
            result = extract_invoice(text)
        except Exception as e:  # OCRError / ExtractionError
            elapsed = time.perf_counter() - start
            print(f"  ERROR: {e} ({elapsed:.1f}s)")
            rows.append((filename, "ERROR", "-", elapsed))
            continue
        elapsed = time.perf_counter() - start
        problems = compare(result["data"], exp)
        for w in result["warnings"]:
            print(f"  warning: {w}")
        if problems:
            for p in problems:
                print(f"  MISMATCH {p}")
            rows.append((filename, "FAIL", len(problems), elapsed))
        else:
            print(f"  OK ({elapsed:.1f}s)")
            rows.append((filename, "OK", 0, elapsed))

    perfect = sum(1 for r in rows if r[1] == "OK")
    total_time = sum(r[3] for r in rows)
    print("\nSummary")
    print(f'  {"file":<36} {"result":<7} {"problems":<8} seconds')
    for filename, result, n, elapsed in rows:
        print(f"  {filename:<36} {result:<7} {str(n):<8} {elapsed:.1f}")
    print(f"\nAccuracy: {perfect}/{len(rows)} ({100 * perfect / len(rows):.0f}%)")
    print(f"Average: {total_time / len(rows):.1f}s per document ({total_time:.0f}s total)")
    return 0 if perfect == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
