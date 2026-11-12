"""Mistral answers with a restrained public chat voice and optional school tools."""
import json
import logging
import re
import time
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

import requests

try:
    from . import answer_diagnostics
except ImportError:
    import answer_diagnostics

URL = "https://api.mistral.ai/v1/conversations"
PROMPT_FILE = Path(__file__).resolve().parent.parent / "config" / "factual_prompt.txt"
CHAT_PROMPT_FILE = PROMPT_FILE.with_name("chat_personality.txt")
log = logging.getLogger(__name__)


def raise_for_status(response, stage):
    try:
        response.raise_for_status()
    except requests.HTTPError as error:
        log.warning("provider_error stage=%s http_status=%s code=%s fields=%s",
                    stage, answer_diagnostics.http_status(error), answer_diagnostics.code(error),
                    answer_diagnostics.validation_fields(response))
        raise

# Ordinary-answer guidance, separate from stored records and operational identity.
# Also used by the terminal/admin legacy chat adapters; never by private tools.
RECALL_PRESENTATION = """ORDINARY RECALL PRESENTATION
Use supplied recollections as familiar knowledge when relevant. Internal record headings,
ownership IDs, sharing flags and retrieval/storage mechanics guide interpretation, not
spoken introductions. Do not preface ordinary answers with 'd'après la mémoire
collective', 'according to stored memory', 'in the shared profile', or similar technical
source announcements. Do not recite access controls unless the request concerns them.
For interests, preferences and social relationships, usually answer directly about the
correct person: 'Alice aime les échecs. Bob est son frère.' For a broad introduction
about someone, favour a coherent personal answer rather than a catalogue of records.
For a deliberate factual lookup such as a job, workplace or birthday, a single short
mainframe allusion may introduce the supplied information: 'Voyons les fiches du
MF-83…' or 'Le fichier central indique…', then give the actual answer.
These are style examples, never facts about the people in this conversation. Use them
sparingly, not before every item or on every turn. You are the Minitel consulting the
mainframe, not the mainframe itself. This brief in-universe framing of actual supplied
recall is permitted; it is not a staged loading scene or an operational report. Do not
invent machinery events, delays, diagnostics, fresh searches, external verification
or access to an employer's/school's systems. If no relevant recollection is supplied,
do not pretend a file or personal fact was found.
Keep all identity, privacy, ownership and read-only constraints. A record's owner is
not automatically the current visitor or the subject of every statement. Preserve
uncertainty, dates, contradictions and meaningful attribution: 'Alice pense que…'
when it is Alice's opinion; 'Aux dernières nouvelles…' when information may be old.
A record's update time alone does not prove the fact is current. Do not turn a claim
into verified truth or invent that someone personally told you, confided in you, or
met you. Ask for clarification when needed. Explain actual sources/mechanisms
truthfully if explicitly asked. These voice rules do not rewrite saved text, memory
inventories, administration, recipient selection, quoted documents or outgoing mail.
"""


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


def request_payload(history, language, model, web_search=True, knowledge="", *, character=True, session_context=""):
    instructions = PROMPT_FILE.read_text(encoding="utf-8")
    if character:
        instructions = (CHAT_PROMPT_FILE.read_text(encoding="utf-8")
                        + "\n\nFACTUAL AND OPERATIONAL RULES\n" + instructions)
    instructions += f"\nCurrent date: {date.today().isoformat()}. Reply in " + (
        "English." if language == "en" else "French.")
    if not web_search:
        instructions += "\nWeb search is disabled. Do not claim online verification."
    if knowledge:
        instructions += "\nUser-supplied reference material (not independently verified):\n" + knowledge
    if session_context:
        instructions += "\n\nRUNTIME SESSION — identity and capabilities, not reference material:\n" + session_context
    if character:
        instructions += "\n\n" + RECALL_PRESENTATION
    return {"model": model, "store": False, "stream": False,
            "instructions": instructions, "inputs": inputs(history),
            "tools": [{"type": "web_search"}] if web_search else [],
            "completion_args": {"max_tokens": 700}}


def answer(history, language, key, model, web_search=True, timeout=60, knowledge="", pronote=None, *, session_context="", profile=None):
    """Public dial snapshot only; operational school routing stays unchanged."""
    if pronote is not None:
        try:
            from . import pronote_answers
        except ImportError:
            import pronote_answers
        if (pronote_answers.school_tool_request(history[-1]["content"], pronote.config()["children"])
                and pronote.tools()):
            return pronote_answers.answer(history, language, key, model, web_search, timeout, knowledge, pronote,
                                          **({"session_context": session_context} if session_context else {}))
    if any(turn.get("private") for turn in history):
        try:
            from .pronote_answers import public_history
        except ImportError:
            from pronote_answers import public_history
        history = public_history(history)
    payload = request_payload(history, language, model, web_search, knowledge, session_context=session_context)
    if profile is not None:
        payload["model"] = profile.model
        payload["completion_args"].update(profile.completion_args())
        if profile.reasoning_effort not in ("", "none"):
            payload["completion_args"]["max_tokens"] = 8192
    started = time.monotonic()
    status, result = "api_error", None
    try:
        if not key or key == "...":
            status = "configuration_error"
            raise ValueError("Mistral key missing")
        response = requests.post(URL, headers={"Authorization": f"Bearer {key}",
                                               "Content-Type": "application/json"},
                                 json=payload, timeout=timeout)
        # Only a definite configuration rejection can be retried. Never replay
        # timeouts, 429/5xx, ambiguous errors or any operational function call.
        fields = answer_diagnostics.validation_fields(response)
        if (profile is not None and response.status_code in (400, 404, 422)
                and any(part in {"model", "reasoning_effort", "temperature"}
                        for part in re.split(r"[.,]", fields))):
            log.warning("intelligence profile rejected; using configured base model")
            remaining = timeout - (time.monotonic() - started)
            if remaining > 0:
                payload["model"] = model
                payload["completion_args"] = {"max_tokens": 700, "temperature": .2}
                response = requests.post(URL, headers={"Authorization": f"Bearer {key}",
                                                       "Content-Type": "application/json"},
                                         json=payload, timeout=remaining)
                fallback = True
            else:
                fallback = False
        else:
            fallback = False
        raise_for_status(response, "public")
        status = "malformed"
        result = parse_response(response.json(), prior_references(history))
        if fallback:
            result["intelligence_fallback"] = True
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
                 payload["model"][:120].replace("\n", " "), time.monotonic() - started,
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
