"""Reported default-profile claims and physical memory navigation; no live APIs."""

from queue import Queue
from unittest.mock import Mock

import pytest
from test_memory_administration import change, selected
from test_mistral_answers import ordinary
from test_terminal_startup import EndOfInput, FakeSerial, fn, screen_text

from services import admin_ui as admin
from services import minitel_chatgpt as chat
from services import mistral_answers as engine
from services import onboarding, pronote_answers


@pytest.mark.parametrize("command", [
    "Souviens toi que le foyer possède un Thermomix",
    "Souviens-toi que le foyer possède un Thermomix",
    "Retiens que le foyer possède un Thermomix",
    "Mémorise que le foyer possède un Thermomix",
    "Peux-tu mémoriser que le foyer possède un Thermomix ?",
    "Enregistre que le foyer possède un Thermomix",
    "Store this fact: the household owns a Thermomix",
])
def test_default_write_requests_return_local_readonly_without_claiming_or_writing(command, monkeypatch):
    subject = selected("")
    public = Mock(side_effect=AssertionError("Must not generate a fake save"))
    monkeypatch.setattr(chat.mistral_answers, "answer", public)
    try:
        before = subject.store.read(subject.key)
        result = chat.call_answer("persona", [{"role": "user", "content": command}],
                                  "fr", ("conversations", False, 30), "", memory=subject)
        assert result["memory"] and "lecture seule" in result["display"]
        assert subject.store.read(subject.key) == before
        subject.model.plan.assert_not_called()
        public.assert_not_called()
    finally:
        subject.close()


@pytest.mark.parametrize("command", ["Souviens-toi", "Mémorise", "Enregistre", "Store"])
@pytest.mark.parametrize("welcome", [True, False])
def test_named_imperatives_bypass_welcome_and_confirm_real_private_save(command, welcome, monkeypatch):
    monkeypatch.setenv("MINITEL_ONBOARDING", "1" if welcome else "0")
    subject = selected("Alice")
    subject.model.plan.return_value = {"operation": "save", "kind": "fact", "text": "User likes robots."}
    try:
        result = chat.call_answer("persona", [{"role": "user", "content": command + " que j'aime les robots"}],
                                  "fr", ("conversations", False, 30), "", memory=subject)
        assert result["memory"] and not result.get("onboarding")
        saved = subject.store.read(subject.key)["items"]
        assert len(saved) == 1 and saved[0]["text"] == "User likes robots." and not saved[0]["shared"]
        subject.model.plan.assert_called_once()
        subject.model.client.complete.assert_not_called()
        assert onboarding.bypass(command + " que j'aime les robots")
    finally:
        subject.close()


@pytest.mark.parametrize("name", ["", "Charlie"])
@pytest.mark.parametrize("shared", [True, False])
def test_actual_terminal_payload_identifies_speaker_even_without_recalled_facts(name, shared, monkeypatch):
    owner, subject = selected("Alice"), selected(name)
    request = Mock()
    request.return_value.json.return_value = ordinary("Synthetic reply about Alice.")
    monkeypatch.setattr(engine.requests, "post", request)
    monkeypatch.setattr(chat, "PROVIDER", "mistral")
    monkeypatch.setattr(chat, "MISTRAL_KEY", "synthetic-key")
    try:
        if shared:
            change(owner, text="User is 10 years old.", share=True)
        result = chat.call_answer("persona", [{"role": "user", "content": "Parle-moi d'Alice"}],
                                  "fr", ("conversations", False, 30), "", memory=subject)
        assert "Synthetic reply" in result["display"]
        instructions = request.call_args.kwargs["json"]["instructions"]
        runtime = instructions.split("RUNTIME SESSION —", 1)[1]
        assert 'Selected user: "' + subject.username + '"' in runtime
        assert "profile_id=" + str(subject.user.id) in runtime
        assert "do not call a shared owner the active profile" in runtime
        assert "no memory write tool" in runtime
        assert ("no named person is logged in" in runtime) == (name == "")
        assert ("SHARED PROFILE FACTS" in instructions) == shared
        assert ("User is 10 years old." in instructions) == shared
        subject.model.plan.assert_not_called()
    finally:
        owner.close()
        subject.close()


def test_browser_default_payload_has_runtime_identity_outside_shared_reference_material(console, monkeypatch):
    client, headers, _ = console
    owner = selected("Alice")
    change(owner, text="User's brother is Bob.", share=True)
    request = Mock()
    request.return_value.json.return_value = ordinary("Synthetic reply.")
    monkeypatch.setattr(engine.requests, "post", request)
    monkeypatch.setattr(admin, "llm_provider", lambda: "mistral")
    monkeypatch.setattr(admin, "mistral_key", lambda: "synthetic-key")
    monkeypatch.setattr(admin, "read_env", lambda: {"MISTRAL_ANSWER_API": "conversations"})
    try:
        result = client.post("/test-preset", headers=headers, json={
            "preset_key": "main", "memory_user": "", "message": "Parle-moi d'Alice"})
        assert result.json["ok"] and "Synthetic" in result.json["answer"]
        instructions = request.call_args.kwargs["json"]["instructions"]
        assert '"owner_name": "Alice"' in instructions
        assert 'Selected user: "MinitelGPT"; profile_id=1' in instructions.split("RUNTIME SESSION —")[1]
        assert "no named person is logged in" in instructions
    finally:
        owner.close()


@pytest.mark.parametrize("command", ["Souviens-toi que j'ai un Thermomix", "Mémorise que j'ai un Thermomix",
                                     "Peux-tu enregistrer que j'ai un Thermomix ?"])
def test_browser_default_write_is_readonly_without_calling_general_model(console, monkeypatch, command):
    client, headers, _ = console
    public = Mock(side_effect=AssertionError("Must not generate a fake save"))
    monkeypatch.setattr(admin, "llm_answer", public)
    result = client.post("/test-preset", headers=headers, json={
        "preset_key": "main", "memory_user": "", "message": command})
    assert result.json["ok"] and "lecture seule" in result.json["answer"]
    public.assert_not_called()


def test_school_planner_and_public_fallback_preserve_session_identity(monkeypatch):
    response = Mock()
    response.json.return_value = ordinary("Public answer.")
    request = Mock(return_value=response)
    monkeypatch.setattr(engine.requests, "post", request)
    backend = Mock()
    backend.config.return_value = {"children": {}}
    backend.tools.return_value = [{"type": "function", "function": {"name": "pronote_list_children"}}]
    result = pronote_answers.answer([{"role": "user", "content": "Explain light"}], "en", "synthetic-key",
                                    "synthetic-model", False, 30, "", backend,
                                    session_context="Selected user: Charlie. No memory write tool.")
    assert result["text"] == "Public answer." and request.call_count == 2
    for call in request.call_args_list:
        assert "Selected user: Charlie" in call.kwargs["json"]["instructions"]
    backend.execute.assert_not_called()


@pytest.mark.parametrize("rows,keys,expected", [
    (1, b"MEMOIRE", 2),
    (22, b"MEMOIRE", 2),  # A new command on the first page, without SUITE.
    (22, fn(chat.K_SUITE) + fn(chat.K_RETOUR) + b"MEMOIRE", 2),
])
def test_real_memory_loop_accepts_input_on_every_page_and_always_shows_navigation(rows, keys, expected, monkeypatch):
    subject = selected("Alice")
    subject.remember_language("fr")
    change(subject, text="\n".join("MEMORY ROW " + str(i) for i in range(rows)))
    uart = FakeSerial(b"MEMOIRE" + fn(chat.K_ENVOI) + keys + fn(chat.K_ENVOI) + fn(chat.K_SOMMAIRE))
    term = chat.Term(uart)
    monkeypatch.setenv("GMAIL_ENABLED", "0")
    monkeypatch.setattr(chat.time, "sleep", lambda _: None)
    monkeypatch.setattr(chat, "wait_connection", lambda _: None)
    monkeypatch.setattr(chat, "prepare_boot", lambda _: [])
    monkeypatch.setattr(chat, "show_boot", lambda *args: "done")
    monkeypatch.setattr(chat.deployment, "available", lambda _: False)
    monkeypatch.setattr(chat, "select_memory_user", lambda *args, **kw: (subject, "envoi"))
    public = Mock(side_effect=AssertionError("Native inventory needs no model"))
    monkeypatch.setattr(engine, "answer", public)

    def synchronous(*args, **kwargs):
        result = chat.call_answer(*args, **kwargs)
        output = Queue()
        output.put((result, None))
        return Mock(is_alive=lambda: False), output, chat.time.monotonic()

    start = Mock(side_effect=synchronous)
    monkeypatch.setattr(chat, "start_answer", start)
    with pytest.raises(EndOfInput):
        chat.run(term)
    shown = screen_text(uart.output)
    assert start.call_count == expected
    assert "SUITE:suiv RETOUR:prec" in shown and "Commande + ENVOI / SOMMAIRE" in shown
    assert "Page 1/" in shown
    if rows == 1:
        assert "Page 1/1" in shown
    public.assert_not_called()
    subject.model.plan.assert_not_called()
