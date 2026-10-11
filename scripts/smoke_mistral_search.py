#!/usr/bin/env python3
"""Optional paid Pi probe: explicit search, source follow-up, ordinary answer.

Check current tool prices, account access and the provider spending cap first.
Capture only public canned answers, references and bounded execution/usage fields;
no credentials, conversation IDs, tool arguments or private reasoning. Review the
capture before sharing. This is not physical-terminal validation.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from services import mistral_answers as engine


def redacted(data):
    outputs = []
    for entry in data.get("outputs", []):
        kind = entry.get("type")
        if kind == "message.output":
            content = entry.get("content")
            if isinstance(content, list):
                content = [{key: value for key, value in chunk.items()
                            if key in ("type", "text", "tool", "title", "url")}
                           for chunk in content if chunk.get("type") in ("text", "tool_reference")]
            outputs.append({"type": kind, "role": entry.get("role", "assistant"), "content": content})
        elif kind == "tool.execution":
            item = {key: entry[key] for key in ("type", "name", "completed_at") if key in entry}
            info = entry.get("info") or {}
            if info.get("error") or info.get("status") in ("failed", "error"):
                item["info"] = {"status": "failed"}
            outputs.append(item)
        else:
            # Preserve unexpected entry types for contract diagnosis, not their contents.
            outputs.append({"type": kind})
    usage = data.get("usage") or {}
    counters = {key: value for key, value in usage.items() if key in (
        "prompt_tokens", "completion_tokens", "total_tokens", "connector_tokens") and isinstance(value, int)}
    return {"outputs": outputs, "usage": counters}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-dir", type=Path, help="Save redacted real response fixtures")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    key = os.getenv("MISTRAL_KEY", "")
    if not key or key == "...":
        parser.error("Set MISTRAL_KEY on the Pi; no call made")
    api, enabled, timeout = engine.settings(os.environ)
    if api != "conversations" or not enabled:
        parser.error("Enable Conversations and basic web search for this probe")
    model = os.getenv("MISTRAL_MODEL", "mistral-small-latest")
    history = []
    questions = [
        "Search the web: what is the latest stable Python release today? Cite official sources.",
        "What does the first source say about that release?",
        "Explain why 17 multiplied by 23 equals 391.",
    ]
    for index, question in enumerate(questions, 1):
        candidate = history + [{"role": "user", "content": question}]
        started = time.monotonic()
        try:
            response = requests.post(engine.URL, headers={"Authorization": f"Bearer {key}"},
                                     json=engine.request_payload(candidate, "en", model), timeout=timeout)
            response.raise_for_status()
            data = response.json()
            if args.capture_dir:
                args.capture_dir.mkdir(parents=True, exist_ok=True)
                (args.capture_dir / f"response-{index}.json").write_text(
                    json.dumps(redacted(data), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            result = engine.parse_response(data)
            if engine.explicit_search(question) and not result["search"]:
                result["status"] = "search_not_executed"
            print(f'{index}: model={model} elapsed={time.monotonic() - started:.2f}s '
                  f'status={result["status"]} searches={len(result["search"])} '
                  f'refs={len(result["references"])} usage={redacted(data)["usage"]}')
            notice = engine.notice(result["status"], "en")
            print(notice or engine.render(result, full_urls=True))
            if notice:
                return 1
            history = engine.bound_history(candidate + [{"role": "assistant", "content": result["text"],
                                                        "chunks": result["chunks"], "references": result["references"]}])
        except (requests.RequestException, ValueError, TypeError, OSError) as error:
            print(f"Probe failed: {type(error).__name__}. No retry or endpoint fallback.", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
