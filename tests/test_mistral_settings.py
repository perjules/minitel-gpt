"""Model overrides use synthetic settings; no paid API calls."""

import json
import time
from unittest.mock import Mock

import pytest
import requests

from services import gmail_mail, memory_session
from services import mistral_settings as settings


def test_inheritance_and_general_tasks_ignore_gmail_overrides():
    env = {"MISTRAL_KEY": "synthetic-key", "MISTRAL_MODEL": "general",
           "MISTRAL_GMAIL_MODEL": "mail", "MISTRAL_GMAIL_DRAFT_MODEL": "mistral-large-4-0",
           "MISTRAL_GMAIL_DRAFT_REASONING_EFFORT": "medium"}
    assert settings.resolve(env)["model"] == "general"
    assert settings.resolve(env)["reasoning_effort"] == ""
    assert settings.resolve(env, service="gmail")["model"] == "mail"
    assert settings.resolve(env, service="gmail", draft=True) == {
        "key": "synthetic-key", "model": "mistral-large-4-0", "reasoning_effort": "medium"}
    env.update(MISTRAL_GMAIL_DRAFT_MODEL="", MISTRAL_GMAIL_DRAFT_REASONING_EFFORT="")
    assert settings.resolve(env, service="gmail", draft=True)["model"] == "mail"
    env["MISTRAL_GMAIL_MODEL"] = ""
    assert settings.resolve(env, service="gmail", draft=True)["model"] == "general"
    assert settings.resolve({})["model"] == "mistral-small-latest"


@pytest.mark.parametrize("model,effort,valid", [
    ("mistral-large-4-0", "medium", True), ("mistral-large-4", "none", False),
    ("mistral-small-2603", "high", True), ("mistral-small-latest", "medium", False),
    ("mistral-medium-3-5", "none", True), ("mistral-large-2512", "high", False),
    ("custom-model", "", True), ("custom-model", "high", False),
    ("bad\nmodel", "", False),
])
def test_reasoning_is_validated_for_the_effective_model(model, effort, valid):
    env = {"MISTRAL_GMAIL_DRAFT_MODEL": model, "MISTRAL_GMAIL_DRAFT_REASONING_EFFORT": effort}
    if valid:
        assert settings.resolve(env, service="gmail", draft=True)["reasoning_effort"] == effort
    else:
        with pytest.raises(settings.SettingsError):
            settings.resolve(env, service="gmail", draft=True)


def test_actual_mail_tasks_use_correct_model_and_memory_stays_general(monkeypatch):
    monkeypatch.setenv("MISTRAL_KEY", "synthetic-key")
    monkeypatch.setenv("MISTRAL_MODEL", "general")
    monkeypatch.setenv("MISTRAL_GMAIL_MODEL", "mail")
    monkeypatch.setenv("MISTRAL_GMAIL_DRAFT_MODEL", "mistral-large-4-0")
    response = Mock()
    response.json.return_value = {"choices": [{"finish_reason": "stop", "message": {"content":
        '{"subject":"Objet","body":"Bonjour"}'}}]}
    post = Mock(return_value=response)
    monkeypatch.setattr(gmail_mail.requests, "post", post)
    model = gmail_mail.MailModel()
    deadline = time.monotonic() + 60
    model.composition("oui sous réserve", {}, deadline)
    assert post.call_args.kwargs["json"]["model"] == "mistral-large-4-0"
    model.complete("Summary", {}, deadline)
    assert post.call_args.kwargs["json"]["model"] == "mail"
    response.json.return_value['choices'][0]['message']['content'] = '{"status":"no_match","ids":[]}'
    model.resolve("Alice", [], deadline)
    assert post.call_args.kwargs["json"]["model"] == "mail"
    memory_session.MemoryModel().client.complete("Memory", {}, deadline)
    assert post.call_args.kwargs["json"]["model"] == "general"


def test_original_request_survives_lossy_or_fabricated_router_output(monkeypatch):
    question = "Écris à alice@example.org : oui, mais seulement après réception du devis."
    model = gmail_mail.MailModel()
    monkeypatch.setattr(model, "complete", Mock(return_value=json.dumps({
        "action": "compose", "recipient": "alice@example.org", "instruction": "accepter",
        "original_request": "fabricated instructions"})))
    route = model.route(question, time.monotonic() + 30)
    session = gmail_mail.MailSession(client=Mock(), model=model)
    compose = Mock(return_value={"display": "draft"})
    monkeypatch.setattr(session, "compose", compose)
    monkeypatch.setattr(model, "route", Mock(return_value=route))
    session.perform(question, None, session.epoch, time.monotonic() + 30)
    assert compose.call_args.args[:2] == ("alice@example.org", question)


def test_reasoning_payload_parses_only_final_json_and_logs_no_private_content(monkeypatch, caplog):
    env = {"MISTRAL_KEY": "PRIVATE-KEY", "MISTRAL_GMAIL_DRAFT_MODEL": "mistral-large-4-0",
           "MISTRAL_GMAIL_DRAFT_REASONING_EFFORT": "medium"}
    response = Mock()
    response.json.return_value = {"choices": [{"finish_reason": "stop", "message": {"content": [
        {"type": "thinking", "thinking": [{"type": "text", "text": "PRIVATE-THINKING"}]},
        {"type": "text", "text": '{"subject":"Objet",'},
        {"type": "text", "text": '"body":"PRIVATE-BODY"}'}]}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 600, "private": "PRIVATE-USAGE"}}
    post = Mock(return_value=response)
    monkeypatch.setattr(gmail_mail.requests, "post", post)
    model = gmail_mail.MailModel(env=lambda: env)
    caplog.set_level("INFO", logger=gmail_mail.__name__)
    subject, body = model.composition("PRIVATE-INSTRUCTION", {}, time.monotonic() + 60)
    assert subject == "Objet" and "PRIVATE-BODY" in body and "THINKING" not in body
    payload = post.call_args.kwargs["json"]
    assert payload['model'] == 'mistral-large-4-0' and payload['reasoning_effort'] == 'medium'
    assert payload['max_tokens'] == 8192 and post.call_args.kwargs['timeout'] == 45
    assert 'PRIVATE' not in caplog.text
    assert 'model=mistral-large-4-0' in caplog.text and "'completion_tokens': 600" in caplog.text
    # The whole-operation deadline still caps a reasoning request.
    model.composition("short", {}, time.monotonic() + 2)
    assert 0 < post.call_args.kwargs['timeout'] <= 2


@pytest.mark.parametrize("content,finish", [
    ([{"type": "thinking", "thinking": "private"}], "stop"),
    ([{"type": "text", "text": {"body": "bad"}}], "stop"),
    ([{"type": "tool_call", "text": "bad"}], "stop"),
    (None, "stop"), ("partial", "length"),
])
def test_reasoning_never_salvages_incomplete_or_malformed_answers(monkeypatch, content, finish):
    response = Mock()
    response.json.return_value = {"choices": [{"finish_reason": finish, "message": {"content": content}}]}
    post = Mock(return_value=response)
    monkeypatch.setattr(gmail_mail.requests, "post", post)
    with pytest.raises(gmail_mail.GmailError, match="generation_incomplete"):
        gmail_mail.MailModel(settings=lambda: ('key', 'model')).complete('x', {}, time.monotonic() + 5)
    assert post.call_count == 1


def test_invalid_reasoning_or_rejected_model_never_falls_back(monkeypatch):
    env = {"MISTRAL_KEY": "key", "MISTRAL_GMAIL_DRAFT_MODEL": "mistral-large-4-0",
           "MISTRAL_GMAIL_DRAFT_REASONING_EFFORT": "none"}
    post = Mock()
    monkeypatch.setattr(gmail_mail.requests, "post", post)
    model = gmail_mail.MailModel(env=lambda: env)
    with pytest.raises(gmail_mail.GmailError, match="invalid_gmail_draft_reasoning_effort"):
        model.composition("x", {}, time.monotonic() + 5)
    post.assert_not_called()
    env['MISTRAL_GMAIL_DRAFT_REASONING_EFFORT'] = 'medium'
    post.return_value.raise_for_status.side_effect = requests.HTTPError(
        'PRIVATE-RESPONSE', response=Mock(status_code=400))
    with pytest.raises(gmail_mail.GmailError, match="generation_configuration_rejected"):
        model.composition("x", {}, time.monotonic() + 5)
    assert post.call_count == 1
