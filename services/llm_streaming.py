"""Public-text SSE adapters; no local tool executor or stored conversation.

Wire reference: https://docs.mistral.ai/openapi.yaml (6 November 2026),
MessageOutputEvent, ToolExecution*Event and ResponseDoneEvent.
"""

import json
from urllib.parse import urlsplit

from . import mistral_answers as engine
from .stream_sources import MAX_FRAME, ProcessSource


def command(text):
    fields = text.strip().split(None, 1)
    if not fields or fields[0].casefold() != "stream":
        return None
    prompt = fields[1].strip() if len(fields) == 2 else ""
    parts = prompt.split(None, 1)
    web = bool(parts and parts[0].casefold() == "web")
    return {"prompt": (parts[1].strip() if len(parts) == 2 else "") if web else prompt, "web": web}


def events(chunks):
    """Bounded SSE frames, including split UTF-8, CRLF, comments and multiline data."""
    line = bytearray()
    event, data, size, carriage, first = "", [], 0, False, True
    for chunk in chunks:
        for byte in chunk:
            if carriage:
                carriage = False
                if byte == 10:
                    continue
            if byte not in (10, 13):
                line.append(byte)
                if len(line) + size > MAX_FRAME:
                    raise ValueError("SSE frame too large")
                continue
            carriage = byte == 13
            text = line.decode("utf-8-sig" if first else "utf-8")
            first = False
            line.clear()
            if not text:
                if data:
                    yield event, "\n".join(data)
                event, data, size = "", [], 0
            elif not text.startswith(":"):
                field, _, value = text.partition(":")
                value = value.removeprefix(" ")
                if field == "event":
                    event = value
                elif field == "data":
                    data.append(value)
                    size += len(value.encode("utf-8")) + 1
    if line or data:
        raise ValueError("Truncated SSE frame")


def _index(data, name):
    value = data.get(name, 0)
    if type(value) is not int or not 0 <= value < 10000:
        raise ValueError("Invalid stream index")
    return value


class Conversations:
    def __init__(self, history, language, web_search):
        self.history, self.language, self.web_search = history, language, web_search
        self.entries, self.ids, self.sources = {}, {}, {}
        self.done, self.result, self.last_message = False, None, None

    def _entry(self, data, kind):
        index, identity = _index(data, "output_index"), data.get("id")
        if not isinstance(identity, str) or not identity or len(identity) > 256:
            raise ValueError("Missing stream entry id")
        if identity in self.ids and self.ids[identity] != index:
            raise ValueError("Conflicting stream entry id")
        self.ids[identity] = index
        entry = self.entries.setdefault(index, {"type": kind, "id": identity})
        if entry["type"] != kind or entry["id"] != identity:
            raise ValueError("Conflicting stream entry")
        return entry

    def feed(self, event, data):
        text = []
        if event == "conversation.response.started":
            return text
        if event == "message.output.delta":
            if data.get("role", "assistant") != "assistant":
                raise ValueError("Unexpected streamed role")
            entry = self._entry(data, "message.output")
            chunks = entry.setdefault("chunks", {})
            content = data.get("content")
            content = {"type": "text", "text": content} if isinstance(content, str) else content
            if not isinstance(content, dict):
                raise TypeError("Malformed streamed content")
            kind = content.get("type")
            if kind == "thinking":
                return text  # Do not retain or emit hidden reasoning.
            index = _index(data, "content_index")
            if kind == "text" and isinstance(content.get("text"), str):
                chunk = chunks.setdefault(index, {"type": "text", "text": ""})
                if chunk.get("type") != "text":
                    raise ValueError("Conflicting streamed chunk")
                chunk["text"] += content["text"]
                if self.last_message is not None and self.last_message != entry["id"]:
                    text.append("\n\n[Message du modèle - provisoire]\n")
                self.last_message = entry["id"]
                text.append(content["text"])
            elif kind == "tool_reference":
                url, title = content.get("url"), content.get("title")
                if content.get("tool") != "web_search" or not isinstance(url, str) or not isinstance(title, str):
                    raise ValueError("Malformed streamed reference")
                parsed = urlsplit(url)
                if (parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password
                        or any(ord(char) < 32 for char in url)):
                    raise ValueError("Invalid streamed reference")
                ref = {"type": "tool_reference", "tool": "web_search", "url": url, "title": title}
                if index in chunks and chunks[index] != ref:
                    raise ValueError("Conflicting streamed reference")
                if index not in chunks:
                    chunks[index] = ref
                    self.sources.setdefault(url, len(self.sources) + 1)
                    text.append(f"[{self.sources[url]}]")
            else:
                raise ValueError("Unsupported streamed content")
        elif event in ("tool.execution.started", "tool.execution.delta", "tool.execution.done"):
            if data.get("name") != "web_search" or not self.web_search:
                raise ValueError("Unexpected streamed tool")
            entry = self._entry(data, "tool.execution")
            entry["name"] = "web_search"
            if event == "tool.execution.started":
                if entry.get("started"):
                    raise ValueError("Duplicate tool start")
                entry["started"] = True
                text.append("\n[Recherche web démarrée]\n")
            elif not entry.get("started"):
                raise ValueError("Tool event before start")
            if event == "tool.execution.done":
                if entry.get("completed_at"):
                    raise ValueError("Duplicate tool completion")
                entry["completed_at"] = data.get("created_at") or "observed-stream-completion"
                entry["info"] = data.get("info") or {}
                if not isinstance(entry["info"], dict):
                    raise TypeError("Malformed streamed tool metadata")
                failed = entry["info"].get("error") or entry["info"].get("status") in ("failed", "error")
                text.append("\n[Recherche web en échec]\n" if failed else "\n[Recherche web terminée]\n")
        elif event == "conversation.response.done":
            outputs = []
            for _, entry in sorted(self.entries.items()):
                if entry["type"] == "message.output":
                    outputs.append({"type": "message.output", "role": "assistant",
                                    "content": [chunk for _, chunk in sorted(entry.get("chunks", {}).items())]})
                else:
                    outputs.append(entry)
            result = engine.parse_response({"outputs": outputs, "usage": data.get("usage")},
                                           engine.prior_references(self.history))
            if engine.explicit_search(self.history[-1]["content"]) and not result["search"]:
                result["status"] = "search_not_executed" if self.web_search else "search_disabled"
            if engine.notice(result["status"], self.language):
                raise ValueError("Streamed answer did not pass factual validation")
            if self.sources:
                text.append("\n\nSources:\n" + "\n".join(
                    f"[{number}] {urlsplit(url).hostname.encode('idna').decode('ascii')}"
                    for url, number in self.sources.items()))
            # Transcript markers follow arrival order, which can differ from
            # canonical message/chunk ordering. LINK must match what was shown.
            result["display_references"] = [{"url": url} for url in self.sources]
            self.result, self.done = result, True
        else:
            # Provider errors, functions, handoffs and unsupported tools fail
            # without exposing arguments or retrying an already submitted call.
            raise ValueError("Unsupported or failed provider stream")
        return text


class ChatCompletions:
    def __init__(self):
        self.done, self.finished = False, False
        self.parts, self.result = [], None

    def feed(self, _event, data):
        if "error" in data:
            raise ValueError("Provider stream error")
        choices = data.get("choices")
        if choices == [] and isinstance(data.get("usage"), dict):
            return []
        if not isinstance(choices, list) or len(choices) != 1 or choices[0].get("index", 0) != 0:
            raise ValueError("Malformed streamed choice")
        choice = choices[0]
        delta = choice.get("delta")
        if not isinstance(delta, dict) or delta.get("tool_calls") or delta.get("function_call"):
            raise ValueError("Unsupported streamed tool call")
        if delta.get("role", "assistant") != "assistant":
            raise ValueError("Unexpected streamed role")
        content = delta.get("content") or ""
        if isinstance(content, list):
            if any(not isinstance(chunk, dict) or chunk.get("type") not in ("text", "thinking") for chunk in content):
                raise ValueError("Unsupported streamed content")
            content = "".join(chunk["text"] for chunk in content if chunk["type"] == "text")
        if not isinstance(content, str) or self.finished and content:
            raise ValueError("Malformed streamed content")
        self.parts.append(content)
        reason = choice.get("finish_reason")
        if reason is not None:
            if reason != "stop":
                raise ValueError("Incomplete provider stream")
            self.finished = True
        # reasoning_content and thinking fields never become public text.
        return [content] if content else []

    def end(self):
        if not self.finished or not "".join(self.parts).strip():
            raise ValueError("Missing provider completion")
        text = "".join(self.parts)
        self.result = {"text": text, "chunks": [{"type": "text", "text": text}],
                       "references": [], "search": [], "usage": {}, "status": "ordinary"}
        self.done = True


def run(job, emit):
    """Child only: blocked network reads are killed with this worker."""
    import requests
    protocol = job["protocol"]
    adapter = (Conversations(job["history"], job["language"], job["web_search"])
               if protocol == "conversations" else ChatCompletions() if protocol == "chat_completions" else None)
    if adapter is None:
        raise ValueError("Unsupported stream protocol")
    with requests.post(job["url"], headers={**job["headers"], "Accept": "text/event-stream"},
                       json=job["payload"], stream=True, timeout=(10, None), allow_redirects=False) as response:
        response.raise_for_status()
        if response.status_code != 200 or response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "text/event-stream":
            raise ValueError("Expected an SSE response")
        emit("[Flux modèle - texte provisoire]\n")
        for event, raw in events(response.iter_content(chunk_size=1024)):
            if raw == "[DONE]":
                if protocol != "chat_completions":
                    raise ValueError("Missing Conversations completion")
                adapter.end()
            else:
                data = json.loads(raw)
                if not isinstance(data, dict):
                    raise TypeError("Malformed provider stream")
                kind = data.get("type", event)
                if event and kind != event:
                    raise ValueError("Conflicting SSE event type")
                for text in adapter.feed(kind, data):
                    emit(text)
            if adapter.done:
                emit("\n[Fin du flux validée]\n")
                return adapter.result
        raise ValueError("Provider disconnected before completion")


def mistral(history, language, key, model, *, api="conversations", web_search=False, knowledge=""):
    """Explicit opt-in public source; ordinary chat retains its existing adapter."""
    from .pronote_answers import public_history
    if not key or key == "...":
        raise ValueError("Mistral key missing")
    history = public_history(history)
    if not history or history[-1].get("role") != "user":
        raise ValueError("A public user prompt is required")
    if api == "conversations":
        payload = engine.request_payload(history, language, model, web_search, knowledge)
        url = engine.URL
    elif api == "chat_completions":
        instructions = engine.request_payload(history, language, model, False, knowledge)["instructions"]
        payload = {"model": model, "messages": [{"role": "system", "content": instructions}]
                   + [{"role": turn["role"], "content": turn["content"]} for turn in history], "max_tokens": 700}
        url = "https://api.mistral.ai/v1/chat/completions"
    else:
        raise ValueError("Unsupported Mistral API")
    payload["stream"] = True
    return ProcessSource.api({"url": url, "headers": {"Authorization": f"Bearer {key}"}, "payload": payload,
                              "protocol": api, "history": history, "language": language,
                              "web_search": web_search if api == "conversations" else False})
