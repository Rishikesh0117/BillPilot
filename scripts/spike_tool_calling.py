"""Spike 3A: does the chat model return well-formed tool calls?

Sends ONE chat request with two dummy tools to the configured OpenAI-compatible
endpoint and checks the reply for a well-formed tool call.

Usage (repo root, with LLM_* set in .env):
    python scripts/spike_tool_calling.py [--model MODEL_ID] [--list-models]

Exits 0 if the model returned a well-formed tool call, 1 otherwise.
"""
import argparse
import json
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

load_dotenv()
TIMEOUT = 90

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "add",
            "description": "Add two numbers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "a": {"type": "number"},
                    "b": {"type": "number"},
                },
                "required": ["a", "b"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get the current weather for a city.",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        },
    },
]


def list_models(base, key):
    r = requests.get(
        f"{base}/models", headers={"Authorization": f"Bearer {key}"}, timeout=TIMEOUT
    )
    r.raise_for_status()
    for m in r.json().get("data", []):
        print(m.get("id"))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Spike: test tool calling on the chat model.")
    parser.add_argument("--model", default=None, help="Override LLM_MODEL for this run.")
    parser.add_argument("--list-models", action="store_true", help="List model IDs and exit.")
    args = parser.parse_args(argv)

    try:
        base = os.environ["LLM_BASE_URL"].rstrip("/")
        key = os.environ["LLM_API_KEY"]
        model = args.model or os.environ["LLM_MODEL"]
    except KeyError as e:
        print(f"Missing {e.args[0]} in environment/.env")
        return 2

    if args.list_models:
        try:
            list_models(base, key)
        except requests.RequestException as e:
            print(f"Could not reach endpoint: {e}")
            return 2
        return 0

    print(f"Model: {model}")
    try:
        r = requests.post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "temperature": 0,
                "messages": [{"role": "user", "content": "What is 18 + 25? Use the add tool."}],
                "tools": TOOLS,
                "tool_choice": "auto",
            },
            timeout=TIMEOUT,
        )
    except requests.RequestException as e:
        print(f"LLM request failed: {e}")
        return 2
    if not r.ok:
        print(f"LLM request failed ({r.status_code}): {r.text[:300]}")
        return 2

    try:
        message = r.json()["choices"][0]["message"]
    except (KeyError, IndexError, ValueError):
        print(f"Unexpected LLM response shape: {r.text[:300]}")
        return 1

    calls = message.get("tool_calls") or []
    print(f"tool_calls: {json.dumps(calls)}")
    if message.get("content"):
        print(f"content: {message['content'][:200]}")
    if not calls:
        print("VERDICT: no tool call returned")
        return 1
    known = {t["function"]["name"] for t in TOOLS}
    for call in calls:
        name = (call.get("function") or {}).get("name")
        raw_args = (call.get("function") or {}).get("arguments", "")
        if name not in known:
            print(f"VERDICT: unknown tool {name!r}")
            return 1
        try:
            json.loads(raw_args or "{}")
        except json.JSONDecodeError:
            print(f"VERDICT: tool {name} has invalid JSON arguments: {raw_args!r}")
            return 1
    print(f"VERDICT: well-formed tool call to {[c['function']['name'] for c in calls]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
