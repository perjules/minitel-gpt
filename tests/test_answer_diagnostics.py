"""Errors stay actionable without copying provider prose or user data."""
import json
from unittest.mock import Mock

import pytest
import requests

from services import answer_diagnostics as diagnostics
from services import minitel_chatgpt as chat
from services import mistral_answers as engine
from services import pronote_answers


def rejected_response(status=422):
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps({"detail": [
        {"loc": ["body", "inputs", 2, "message.output", "content", 1, "url"],
         "msg": "SECRET prompt and credentials", "input": "SECRET"},
        {"loc": ["body", "SECRET private field"], "msg": "SECRET"},
    ], "message": "SECRET", "code": "SECRET", "param": "SECRET"}).encode()
    return response


@pytest.mark.parametrize("status,expected", [
    (400, "invalid_request"), (422, "invalid_request"), (401, "authentication"),
    (429, "rate_limit"), (503, "provider_unavailable"),
])
def test_http_failures_are_classified_and_exclude_provider_prose(status, expected, caplog):
    response = rejected_response(status)
    with pytest.raises(requests.HTTPError) as raised:
        engine.raise_for_status(response, "public")
    assert diagnostics.code(raised.value) == expected
    assert f"http_status={status}" in caplog.text
    assert "fields=body.inputs.2.message.output.content.1.url" in caplog.text
    assert "SECRET" not in caplog.text
    assert "SECRET" not in diagnostics.notice(raised.value, "en")


@pytest.mark.parametrize("body", [None, [], {"detail": "SECRET"}, {"param": "inputs.SECRET"}])
def test_malformed_provider_diagnostics_never_replace_original_error(body):
    response = Mock()
    response.json.return_value = body
    assert diagnostics.validation_fields(response) == "-"
    response.json.side_effect = ValueError("SECRET")
    assert diagnostics.validation_fields(response) == "-"


@pytest.mark.parametrize("error,expected", [
    (requests.Timeout("SECRET"), "timeout"), (requests.ConnectionError("SECRET"), "network"),
    (ValueError("Expected one final assistant entry"), "ambiguous_final"),
    (TypeError("SECRET"), "invalid_response"),
])
def test_terminal_error_is_specific_and_never_becomes_conversation_history(error, expected, caplog):
    output = chat.Queue()
    output.put((None, error))
    response, action = chat.wait_answer(Mock(), (Mock(), output, chat.time.monotonic()), 30, language="en")
    assert action == "error" and response["turn"] is None
    assert expected in response["display"]
    assert f"code={expected}" in caplog.text
    assert "SECRET" not in response["display"] + caplog.text


def test_news_prompt_bypasses_school_and_logs_the_actual_http_failure(monkeypatch, caplog):
    backend = Mock()
    backend.config.return_value = {"children": {"Alex": {}}}
    post = Mock(return_value=rejected_response())
    monkeypatch.setattr(engine.requests, "post", post)
    with pytest.raises(requests.HTTPError):
        engine.answer([{"role": "user", "content": "give me a rundown of the news"}],
                      "en", "synthetic-key", "model", pronote=backend)
    post.assert_called_once()
    assert post.call_args.kwargs["json"]["tools"] == [{"type": "web_search"}]
    backend.tools.assert_not_called()
    assert "provider_error stage=public http_status=422" in caplog.text
    assert "SECRET" not in caplog.text


def test_school_http_failure_identifies_its_stage(monkeypatch, caplog):
    backend = Mock()
    backend.config.return_value = {"children": {"Alex": {}}}
    monkeypatch.setattr(engine.requests, "post", Mock(return_value=rejected_response()))
    with pytest.raises(requests.HTTPError):
        pronote_answers.answer([{"role": "user", "content": "Notes Alex ?"}],
                               "fr", "synthetic-key", "model", False, 30, "", backend)
    assert "provider_error stage=school http_status=422" in caplog.text


def test_export_rebuilds_events_instead_of_copying_journal_lines():
    journal = (
        "SECRET question and credentials\n"
        "SECRET provider_error stage=public http_status=422 code=invalid_request fields=body.inputs.2.message.output.content.1.url\n"
        "SECRET answer status=api_error error=HTTPError code=unclassified http_status=422\n"
        "SECRET provider_error stage=SECRET http_status=SECRET code=SECRET fields=body.SECRET\n"
        "SECRET answer status=deadline elapsed=SECRET\n"
    )
    events = diagnostics.recent_events(journal)
    assert events[0] == {"event": "provider_error", "stage": "public", "http_status": 422,
                         "code": "invalid_request", "fields": "body.inputs.2.message.output.content.1.url"}
    assert events[1]["http_status"] == 422
    assert events[2] == {"event": "provider_error"}
    assert events[3] == {"event": "deadline"}
    assert "SECRET" not in json.dumps(events)
    assert len(diagnostics.recent_events(journal * 100)) == 20
