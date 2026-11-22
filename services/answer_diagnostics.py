"""Fixed error classifications and shareable diagnostics, never provider prose."""
import re

import requests

LOCAL_CODES = {
    "Sources without observed search execution": "unverified_references",
    "Unexpected web search in private request": "private_web_output",
    "Expected one final assistant entry": "ambiguous_final",
    "Empty factual answer": "empty_answer",
    "Local tool call limit": "local_tool_limit",
    "Mistral key missing": "configuration_error",
    "Malformed Conversations outputs": "malformed_outputs",
    "Malformed Conversations entry": "malformed_entry",
    "Unexpected Conversations output entry": "unexpected_entry",
    "Malformed search metadata": "malformed_search",
    "Unexpected hosted tool": "unexpected_tool",
    "Malformed assistant content": "malformed_content",
    "Malformed assistant chunk": "malformed_chunk",
    "Malformed source reference": "malformed_reference",
    "Invalid source URL": "invalid_source_url",
    "Unsupported assistant chunk": "unsupported_chunk",
    "Malformed provider usage": "malformed_usage",
    "Malformed local tool response": "malformed_tool_response",
    "Malformed local function call": "malformed_function_call",
    "Unexpected private output": "unexpected_private_output",
    "Missing local final answer": "missing_tool_answer",
}
CODES = set(LOCAL_CODES.values()) | {
    "invalid_request", "authentication", "rate_limit", "provider_unavailable",
    "http_error", "timeout", "network", "invalid_response", "unclassified",
}
FIELDS = {
    "body", "model", "reasoning_effort", "temperature", "inputs", "instructions", "tools", "completion_args", "max_tokens",
    "type", "role", "content", "text", "url", "title", "tool", "message.input",
    "message.output", "tool_reference", "function.call", "function.result", "function",
    "name", "parameters", "properties", "required", "arguments", "tool_call_id", "result",
    "store", "stream", "tool_choice", "object", "id", "message", "input", "output", "call",
}


def http_status(error):
    status = getattr(getattr(error, "response", None), "status_code", None)
    return status if type(status) is int and 100 <= status <= 599 else None


def code(error):
    status = http_status(error)
    if status is not None:
        if status in {400, 404, 422}:
            return "invalid_request"
        if status in {401, 402, 403}:
            return "authentication"
        if status == 429:
            return "rate_limit"
        return "provider_unavailable" if status >= 500 else "http_error"
    if isinstance(error, (requests.Timeout, TimeoutError)):
        return "timeout"
    if isinstance(error, requests.ConnectionError):
        return "network"
    if str(error) in LOCAL_CODES:
        return LOCAL_CODES[str(error)]
    return "invalid_response" if isinstance(error, (ValueError, TypeError, KeyError)) else "unclassified"


def validation_fields(response):
    """Keep schema locations only. Even error types/messages can echo user data."""
    try:
        body = response.json()
    except (ValueError, TypeError):
        return "-"
    if not isinstance(body, dict):
        return "-"
    paths = []
    detail = body.get("detail")
    locations = [item.get("loc") for item in detail[:8] if isinstance(item, dict)] if isinstance(detail, list) else []
    if isinstance(body.get("param"), str):
        locations.append(body["param"].split("."))
    for location in locations:
        if (not isinstance(location, (list, tuple)) or not 1 <= len(location) <= 12
                or any(not (isinstance(part, str) and part in FIELDS
                            or type(part) is int and 0 <= part <= 9999) for part in location)):
            continue
        paths.append(".".join(map(str, location)))
    return ",".join(dict.fromkeys(paths)) or "-"


def notice(error, language):
    reason, status = code(error), http_status(error)
    messages = {
        "invalid_request": ("Requête refusée par le fournisseur IA", "AI provider rejected the request"),
        "authentication": ("Accès au fournisseur IA refusé", "AI provider access denied"),
        "rate_limit": ("Limite de requêtes IA atteinte", "AI request rate limit reached"),
        "provider_unavailable": ("Fournisseur IA indisponible", "AI provider unavailable"),
        "network": ("Connexion au fournisseur IA impossible", "Cannot connect to the AI provider"),
        "timeout": ("Délai de réponse IA dépassé", "AI request timed out"),
    }
    message = messages.get(reason, ("Réponse IA inexploitable", "AI response could not be processed"))[language == "en"]
    return (message + (f" (HTTP {status})" if status else "") + ".\n"
            + ("Diagnostic dans Maintenance. Code : " if language != "en" else
               "See Maintenance diagnostics. Code: ") + reason)


def recent_events(journal):
    """Rebuild fixed diagnostic fields instead of exporting arbitrary log lines."""
    events = []
    for line in journal.splitlines():
        if "provider_error " in line:
            event = {"event": "provider_error"}
        elif "answer status=api_error " in line:
            event = {"event": "answer_error"}
        elif "answer status=deadline " in line:
            events.append({"event": "deadline"})
            continue
        else:
            continue
        tokens = dict(re.findall(r"\b(stage|http_status|code|fields)=([^\s]+)", line))
        if tokens.get("stage") in {"public", "school"}:
            event["stage"] = tokens["stage"]
        if tokens.get("code") in CODES:
            event["code"] = tokens["code"]
        if re.fullmatch(r"[1-5][0-9]{2}", tokens.get("http_status", "")):
            event["http_status"] = int(tokens["http_status"])
        fields = tokens.get("fields", "")
        if fields and fields != "-" and len(fields) <= 1000 and all(
                part in FIELDS or re.fullmatch(r"[0-9]{1,4}", part)
                for part in re.split(r"[.,]", fields)):
            event["fields"] = fields
        events.append(event)
    return events[-20:]
