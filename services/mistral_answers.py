"""Mistral's hosted factual engine. No theatrical context; optional local school tools."""
import json
import logging
import re
import time
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

import requests

URL = "https://api.mistral.ai/v1/conversations"
PROMPT_FILE = Path(__file__).resolve().parent.parent / "config" / "factual_prompt.txt"
log = logging.getLogger(__name__)


def settings(env):
    """Small installation-level switches, shared by terminal and admin preview."""
    api = env.get("MISTRAL_ANSWER_API", "conversations").strip().lower()
    if api not in ("conversations", "chat_completions"):
        raise ValueError("MISTRAL_ANSWER_API must be conversations or chat_completions")
    enabled = env.get("MISTRAL_WEB_SEARCH", "1").strip().lower()
    if enabled not in ("1", "0", "true", "false", "yes", "no"):
        raise ValueError("MISTRAL_WEB_SEARCH must be 1 or 0")
    timeout = float(env.get("MISTRAL_REQUEST_TIMEOUT", "60"))
    if not 1 <= timeout <= 300:
        raise ValueError("MISTRAL_REQUEST_TIMEOUT must be between 1 and 300 seconds")
    return api, enabled in ("1", "true", "yes"), timeout


def explicit_search(question):
    if re.search(r"\b(without|no)\s+(web\s+)?search\b|\bsans\s+(recherche|chercher)\b",
                 question, re.IGNORECASE):
        return False
    return bool(re.search(
        r"\b(search|look\s+up|browse|google|recherche\w*|cherche\w*)\b"
        r"|\b(v[eé]rif\w*|verify|check|find)\b.{0,50}\b(web|online|internet|ligne)\b",
        question, re.IGNORECASE,
    ))


def inputs(history):
    """Replay only local factual turns and real references, never tool executions."""
    result = []
    for turn in history:
        if turn["role"] == "assistant" and turn.get("chunks"):
            result.append({"type": "message.output", "role": "assistant",
                           "content": turn["chunks"]})
        else:
            result.append({"role": turn["role"], "content": turn["content"]})
    return result


def prior_references(history):
    """Previously validated public sources, not evidence of a new search."""
    references = {}
    for turn in history:
        if turn.get("role") != "assistant" or turn.get("private"):
            continue
        for ref in turn.get("references", []):
            if isinstance(ref, dict) and isinstance(ref.get("url"), str) and isinstance(ref.get("title"), str):
                references.setdefault(ref["url"], ref["title"])
    return references


def parse_response(data, previous_references=None):
    previous_references = previous_references or {}
    if not isinstance(data, dict) or not isinstance(data.get("outputs"), list):
        raise TypeError("Malformed Conversations outputs")
    search = []
    final = []
    for entry in data["outputs"]:
        if not isinstance(entry, dict):
            raise TypeError("Malformed Conversations entry")
        kind = entry.get("type")
        if kind == "tool.execution":
            # Messages preceding a tool execution are intermediate activity.
            final = []
            if entry.get("name") == "web_search":
                info = entry.get("info") or {}
                if not isinstance(info, dict):
                    raise ValueError("Malformed search metadata")
                search.append({"name": "web_search",
                               "completed": bool(entry.get("completed_at")),
                               "failed": bool(info.get("error")) or
                               info.get("status") in ("failed", "error")})
            else:
                raise ValueError("Unexpected hosted tool")
        elif kind == "message.output" and entry.get("role", "assistant") == "assistant":
            final.append(entry)
        else:
            raise ValueError("Unexpected Conversations output entry")
    # Until a live fixture establishes multi-final semantics, fail recoverably.
    if len(final) != 1:
        raise ValueError("Expected one final assistant entry")
    content = final[0].get("content")
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]
    if not isinstance(content, list):
        raise TypeError("Malformed assistant content")
    chunks, references, by_url = [], [], {}
    for chunk in content:
        if not isinstance(chunk, dict):
            raise TypeError("Malformed assistant chunk")
        kind = chunk.get("type")
        if kind == "text" and isinstance(chunk.get("text"), str):
            chunks.append({"type": "text", "text": chunk["text"]})
        elif kind == "tool_reference":
            url, title = chunk.get("url"), chunk.get("title")
            if (chunk.get("tool") != "web_search" or not isinstance(url, str)
                    or not isinstance(title, str)):
                raise ValueError("Malformed source reference")
            parsed = urlsplit(url)
            if (parsed.scheme not in ("http", "https") or not parsed.hostname
                    or parsed.username or parsed.password
                    or any(ord(c) < 32 for c in url)):
                raise ValueError("Invalid source URL")
            if url not in by_url:
                by_url[url] = len(references) + 1
                references.append({"title": previous_references.get(url, title) if not search else title, "url": url})
            chunks.append({"type": "tool_reference", "tool": "web_search",
                           "title": references[by_url[url] - 1]["title"], "url": url})
        elif kind == "thinking":
            continue  # Documented private reasoning is never answer prose.
        else:
            raise ValueError("Unsupported assistant chunk")
    text = "".join(chunk["text"] for chunk in chunks if chunk["type"] == "text")
    if not text.strip():
        raise ValueError("Empty factual answer")
    if references and not search and any(ref["url"] not in previous_references for ref in references):
        raise ValueError("Sources without observed search execution")
    status = "ordinary"
    if search:
        status = "searched"
        if any(item["failed"] or not item["completed"] for item in search):
            status = "search_incomplete"
        elif not references:
            status = "insufficient_sources"
    usage = data.get("usage") or {}
    if not isinstance(usage, dict):
        raise TypeError("Malformed provider usage")
    return {"text": text, "chunks": chunks, "references": references,
            **({"reused_sources": True} if references and not search else {}),
            "search": search, "usage": usage, "status": status}


def request_payload(history, language, model, web_search=True, knowledge=""):
    instructions = PROMPT_FILE.read_text(encoding="utf-8")
    instructions += f"\nCurrent date: {date.today().isoformat()}. Reply in " + (
        "English." if language == "en" else "French.")
    if not web_search:
        instructions += "\nWeb search is disabled. Do not claim online verification."
    if knowledge:
        instructions += "\nUser-supplied reference material (not independently verified):\n" + knowledge
    return {"model": model, "store": False, "stream": False,
            "instructions": instructions, "inputs": inputs(history),
            "tools": [{"type": "web_search"}] if web_search else [],
            "completion_args": {"max_tokens": 700}}


def answer(history, language, key, model, web_search=True, timeout=60, knowledge="", pronote=None):
    """One complete request; no retry, stored session or endpoint fallback."""
    if pronote is not None and pronote.tools():
        try:
            from . import pronote_answers
        except ImportError:
            import pronote_answers
        return pronote_answers.answer(history, language, key, model, web_search, timeout, knowledge, pronote)
    if any(turn.get("private") for turn in history):
        try:
            from .pronote_answers import public_history
        except ImportError:
            from pronote_answers import public_history
        history = public_history(history)
    payload = request_payload(history, language, model, web_search, knowledge)
    started = time.monotonic()
    status, result = "api_error", None
    try:
        if not key or key == "...":
            status = "configuration_error"
            raise ValueError("Mistral key missing")
        response = requests.post(URL, headers={"Authorization": f"Bearer {key}",
                                               "Content-Type": "application/json"},
                                 json=payload, timeout=timeout)
        response.raise_for_status()
        status = "malformed"
        result = parse_response(response.json(), prior_references(history))
        question = history[-1]["content"]
        if explicit_search(question) and not result["search"]:
            result["status"] = "search_not_executed" if web_search else "search_disabled"
        status = result["status"]
        return result
    finally:
        usage = (result or {}).get("usage", {})
        # Allowlisted counters only: no question, key, URL or arbitrary provider diagnostics.
        counters = {name: usage[name] for name in (
            "prompt_tokens", "completion_tokens", "total_tokens", "connector_tokens")
            if isinstance(usage, dict) and isinstance(usage.get(name), int)}
        log.info("endpoint=conversations model=%s elapsed=%.2f search=%s refs=%d status=%s usage=%s",
                 model[:120].replace("\n", " "), time.monotonic() - started,
                 bool((result or {}).get("search")),
                 len((result or {}).get("references", [])), status, json.dumps(counters))


def render(result, full_urls=False):
    """Add display-only citation markers, preserving every factual text chunk."""
    numbers = {ref["url"]: index for index, ref in enumerate(result["references"], 1)}
    text = "".join(chunk["text"] if chunk["type"] == "text"
                   else f'[{numbers[chunk["url"]]}]' for chunk in result["chunks"])
    if numbers:
        lines = []
        for ref in result["references"]:
            label = (ref["title"] if full_urls else
                     urlsplit(ref["url"]).hostname.encode("idna").decode("ascii"))
            lines.append(f'[{numbers[ref["url"]]}] {label}')
            if full_urls:
                lines.append(ref["url"])
        text += "\n\nSources:\n" + "\n".join(lines)
    return text


def notice(status, language):
    messages = {
        "search_not_executed": ("Recherche web non executee. Information non verifiee.",
                                "Web search was not executed. Information unverified."),
        "search_disabled": ("Recherche web desactivee. Information non verifiee.",
                            "Web search is disabled. Information unverified."),
        "search_incomplete": ("Recherche web incomplete. Information non verifiee.",
                              "Web search incomplete. Information unverified."),
        "insufficient_sources": ("Sources insuffisantes. Information non verifiee.",
                                 "Insufficient sources. Information unverified."),
    }
    return messages.get(status, ("", ""))[language == "en"]


def bound_history(history):
    """Keep up to ten complete turns and 60k characters, dropping oldest pairs."""
    history = history[-20:]
    while history and len(json.dumps(history, ensure_ascii=False)) > 60000:
        history = history[2:]
    return history
