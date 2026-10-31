"""Run OCR + LLM extraction on every sample PDF and score it against samples/expected.json.

Usage (repo root, with LLM_* set in .env):
    python scripts/eval_extraction.py
"""
import json
import sys
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


def main():
    expected = json.loads((SAMPLES / "expected.json").read_text(encoding="utf-8"))
    perfect = 0
    for filename, exp in expected.items():
        print(f"\n{filename}")
        try:
            text = extract_text(str(SAMPLES / filename))["text"]
            result = extract_invoice(text)
        except Exception as e:  # OCRError / ExtractionError
            print(f"  ERROR: {e}")
            continue
        problems = compare(result["data"], exp)
        for w in result["warnings"]:
            print(f"  warning: {w}")
        if problems:
            for p in problems:
                print(f"  MISMATCH {p}")
        else:
            print("  OK")
            perfect += 1
    print(f"\n{perfect}/{len(expected)} documents extracted perfectly")


if __name__ == "__main__":
    main()