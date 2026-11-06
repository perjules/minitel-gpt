"""Recall voice belongs to ordinary answers, not private tools or stored facts."""

import time
from unittest.mock import Mock

import pytest
from test_memory_administration import change, selected
from test_mistral_answers import ordinary

from services import admin_ui as admin
from services import gmail_mail, memory_session, pronote_answers
from services import minitel_chatgpt as chat
from services import mistral_answers as engine


@pytest.mark.parametrize("surface", ["terminal", "browser"])
@pytest.mark.parametrize("provider,api", [("mistral", "conversations"), ("mistral", "chat_completions"),
                                         ("claude", "chat_completions")])
def test_actual_answer_payloads_add_recall_voice_keep_attribution_and_make_one_request(
    surface, provider, api, console, monkeypatch
):
    client, headers, _ = console
    alice, visitor = selected("Alice"), selected("")
    change(alice, text="User enjoys chess and believes Bob dislikes school.", share=True)
    before = alice.store.read(alice.key)
    response = Mock()
    text = "Alice aime les echecs. Alice pense que Bob n'aime pas l'ecole."
    response.json.return_value = (ordinary(text) if provider == "mistral" and api == "conversations" else
                                  {"choices": [{"message": {"content": text}}],
                                   "content": [{"type": "text", "text": text}]})
    post = Mock(return_value=response)
    monkeypatch.setattr(engine.requests, "post", post)
    monkeypatch.setattr(chat, "PROVIDER", provider)
    monkeypatch.setattr(chat, "MISTRAL_KEY", "synthetic-key")
    monkeypatch.setattr(chat, "ANTHROPIC_KEY", "synthetic-key")
    monkeypatch.setattr(admin, "llm_provider", lambda: provider)
    monkeypatch.setattr(admin, "mistral_key", lambda: "synthetic-key")
    monkeypatch.setattr(admin, "anthropic_key", lambda: "synthetic-key")
    monkeypatch.setattr(admin, "read_env", lambda: {"MISTRAL_ANSWER_API": api, "MISTRAL_WEB_SEARCH": "0"})
    try:
        if surface == "terminal":
            result = chat.call_answer("OWNER EDITED PERSONA", [{"role": "user", "content": "Parle-moi d'Alice"}],
                                      "fr", (api, False, 30), "", memory=visitor)
            assert result["display"] == text
        else:
            result = client.post("/test-preset", headers=headers, json={
                "preset_key": "main", "memory_user": "", "message": "Parle-moi d'Alice"})
            assert result.json["ok"] and result.json["answer"] == text
        post.assert_called_once()
        payload = post.call_args.kwargs["json"]
        instructions = (payload["instructions"] if api == "conversations" and provider == "mistral" else
                        payload["system"] if provider == "claude" else payload["messages"][0]["content"])
        assert engine.RECALL_PRESENTATION in instructions
        assert '"owner_name": "Alice"' in instructions
        assert 'Selected user: "MinitelGPT"; profile_id=1' in instructions
        assert "no named person is logged in" in instructions
        assert "not verified facts about another person" in instructions
        assert "no memory write tool" in instructions
        assert alice.store.read(alice.key) == before
    finally:
        with client.session_transaction() as cookie:
            admin.discard_memory(cookie.get("memory_session"))
        alice.close()
        visitor.close()


def test_public_request_skips_school_planner_and_keeps_voice(monkeypatch):
    response = Mock()
    response.json.return_value = ordinary("Synthetic public answer.")
    post = Mock(return_value=response)
    monkeypatch.setattr(engine.requests, "post", post)
    backend = Mock()
    backend.tools.return_value = [{"type": "function", "function": {"name": "pronote_list_children"}}]
    backend.config.return_value = {"children": {}}
    pronote_answers.answer([{"role": "user", "content": "Parle-moi d'Alice"}], "fr", "synthetic-key",
                           "synthetic-model", False, 30, "Quoted recollection", backend,
                           session_context="Selected user: MinitelGPT; profile_id=1. Anonymous visitor.")
    post.assert_called_once()
    instructions = post.call_args.kwargs["json"]["instructions"]
    assert engine.RECALL_PRESENTATION in instructions
    assert "profile_id=1" in instructions
    backend.execute.assert_not_called()


def test_private_school_final_answer_never_receives_recall_theatre(monkeypatch):
    response = Mock()
    response.json.return_value = ordinary("Synthetic school answer.")
    post = Mock(return_value=response)
    monkeypatch.setattr(engine.requests, "post", post)
    backend = Mock()
    backend.tools.return_value = [{"type": "function", "function": {"name": "pronote_list_children"}}]
    backend.config.return_value = {"children": {"Alex": {}}}
    result = pronote_answers.answer([{"role": "user", "content": "Quels devoirs pour Alex ?"}], "fr", "synthetic-key",
                                    "synthetic-model", False, 30, "", backend)
    assert result["private"]
    post.assert_called_once()
    assert engine.RECALL_PRESENTATION not in post.call_args.kwargs["json"]["instructions"]
    backend.execute.assert_not_called()


def test_inventory_planner_and_recipient_context_keep_original_text_without_voice_rules():
    alice, visitor = selected("Alice"), selected("")
    literal = "Citation : d'après la mémoire collective, Bob aime les échecs."
    change(visitor, text=literal, publish=True)
    change(alice, text="User's brother is Bob.", share=True)
    try:
        assert literal in visitor.handle("MEMOIRE")["display"]
        context = visitor.recipient_context("Alice brother")
        assert engine.RECALL_PRESENTATION not in context and "owner_name" in context
        model = gmail_mail.MailModel()
        model.complete = Mock(return_value='{"status":"unique","ids":["bob"]}')
        model.resolve("Bob", [{"id": "bob", "names": ["Bob"], "address": "bob@example.invalid"}],
                      time.monotonic() + 30, context=context)
        assert engine.RECALL_PRESENTATION not in model.complete.call_args.args[0]
        planner = memory_session.MemoryModel()
        planner.client.complete = Mock(return_value='{"operation":"public"}')
        planner.plan("Que sais-tu d'Alice ?", [], False, [], "fr", time.monotonic() + 30,
                     read_only_context=context)
        assert engine.RECALL_PRESENTATION not in planner.client.complete.call_args.args[0]
    finally:
        alice.close()
        visitor.close()
