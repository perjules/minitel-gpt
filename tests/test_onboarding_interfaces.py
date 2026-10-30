"""Welcome through Flask and the real UART loop; provider calls are mocked."""

from queue import Queue
from unittest.mock import Mock

import pytest
from test_onboarding import fact, response
from test_terminal_startup import EndOfInput, FakeSerial, fn, screen_text

from services import admin_ui as admin
from services import calculator, gmail_mail
from services import memory_session as sessions
from services import minitel_chatgpt as chat


@pytest.fixture
def welcome_enabled(monkeypatch):
    monkeypatch.setenv("MINITEL_ONBOARDING", "1")


def test_real_terminal_introduces_before_first_answer_and_resumes_without_boot(
    welcome_enabled, monkeypatch
):
    phases = []

    def complete(self, instructions, data, deadline, **kwargs):
        phases.append(data["phase"])
        if data["phase"] == "first":
            return response(
                reply="Je suis le Minitel, à votre service. Quel âge avez-vous ?"
            )
        if data["current_reply"] == "10":
            return response(
                reply="Quelle matière aimez-vous à l’école ?",
                topic="daily_life",
                covered=["age"],
                memories=[fact("10", event_id=data["current_event_id"])],
            )
        return response(
            reply="Reprenons tranquillement. Quel loisir vous plaît ?",
            topic="interests",
        )

    monkeypatch.setattr(gmail_mail.MailModel, "complete", complete)
    monkeypatch.setattr(chat.time, "sleep", Mock())
    monkeypatch.setattr(
        chat,
        "load_preset",
        lambda: ("OWNER PERSONA", "3615 IA", "Question?", "WAIT", None),
    )
    monkeypatch.setattr(chat, "build_title", lambda _: ["LOGO"])
    monkeypatch.setattr(chat, "factual_knowledge", lambda: "")
    monkeypatch.setattr(chat, "prepare_boot", lambda _: chat.UI["fr"]["boot"])
    monkeypatch.setattr(chat.gmail_mail, "enabled", lambda: False)

    def synchronous(*args, **kwargs):
        result = chat.call_answer(*args, **kwargs)
        output = Queue()
        output.put((result, None))
        return Mock(is_alive=lambda: False), output, chat.time.monotonic()

    monkeypatch.setattr(chat, "start_answer", synchronous)
    ordinary = Mock(
        return_value={
            "status": "ordinary",
            "text": "Bonjour.",
            "chunks": [],
            "references": [],
        }
    )
    monkeypatch.setattr(chat.mistral_answers, "answer", ordinary)
    boot = Mock(wraps=chat.show_boot)
    monkeypatch.setattr(chat, "show_boot", boot)
    keys = (
        fn(chat.K_SOMMAIRE)
        + fn(chat.K_SUITE)
        + b"Camille"
        + fn(chat.K_ENVOI)
        + b"fr"
        + fn(chat.K_ENVOI)
    )
    keys += b"10" + fn(chat.K_ENVOI) + fn(chat.K_SOMMAIRE)
    keys += b"FIN ACCUEIL" + fn(chat.K_ENVOI) + fn(chat.K_SOMMAIRE)
    keys += b"Bonjour" + fn(chat.K_ENVOI)
    uart = FakeSerial(keys)
    with pytest.raises(EndOfInput):
        chat.run(chat.Term(uart))
    output = screen_text(uart.output)
    assert (
        "Quel age avez-vous" in output
        and "matiere" in output
        and "Reprenons tranquillement" in output
    )
    assert phases == ["first", "reply", "resume"]
    assert boot.call_count == 1 and ordinary.call_count == 1
    assert ordinary.call_args.args[0] == [{"role": "user", "content": "Bonjour"}]
    selected = sessions.MemorySession("Camille")
    try:
        assert (
            selected.store.read(selected.key)["items"][0]["text"]
            == "User is 10 years old."
        )
        assert selected.welcome.state()["status"] == "skipped"
    finally:
        selected.close()


def test_welcome_answers_do_not_trigger_school_access_and_explicit_tools_still_work(
    welcome_enabled, tmp_path, monkeypatch
):
    selected = sessions.MemorySession("Child", root=tmp_path, model=Mock())
    try:
        selected.model.client.complete.return_value = response(
            reply="Quelle matière aimez-vous ?", topic="daily_life"
        )
        school = Mock()
        monkeypatch.setattr(chat.pronote, "backend", lambda: school)
        result = chat.call_answer(
            "persona",
            [{"role": "user", "content": "I go to school."}],
            "en",
            ("conversations", True, 60),
            "",
            memory=selected,
        )
        assert result["onboarding"]
        school.config.assert_not_called()
        selected.model.client.complete.reset_mock()
        result = chat.call_answer(
            "persona",
            [{"role": "user", "content": "CALC 2+2"}],
            "fr",
            ("conversations", True, 60),
            "",
            memory=selected,
            calc=calculator.Session(),
        )
        assert result["calculator"]
        school.tools.return_value = []
        school.config.return_value = {"enabled": False, "children": {}}
        result = chat.call_answer(
            "persona",
            [{"role": "user", "content": "MEMOIRE"}],
            "fr",
            ("conversations", True, 60),
            "",
            memory=selected,
        )
        assert result["memory"]
        selected.model.client.complete.assert_not_called()
    finally:
        selected.close()


def test_admin_preview_and_short_answer_use_same_profile_and_no_generic_extraction(
    console, welcome_enabled, monkeypatch
):
    client, headers, _ = console
    model = Mock()
    model.client.complete.return_value = response()
    monkeypatch.setattr(sessions, "MemoryModel", lambda **kwargs: model)
    answer = client.post(
        "/test-preset",
        json={
            "preset_key": "main",
            "message": "",
            "memory_user": "Camille",
            "welcome_start": True,
        },
        headers=headers,
    )
    assert answer.json["ok"] and "âge" in answer.json["answer"]
    model.client.complete.return_value = response(
        reply="Quelle matière aimez-vous ?",
        topic="daily_life",
        covered=["age"],
        memories=[fact("10")],
    )
    answer = client.post(
        "/test-preset",
        json={"preset_key": "main", "message": "10", "memory_user": "Camille"},
        headers=headers,
    )
    assert answer.json["ok"] and "matière" in answer.json["answer"]
    with client.session_transaction() as cookie:
        selected = admin.MEMORY_SESSIONS[cookie["memory_session"]]["memory"]
    with selected.registry.connection() as db:
        assert db.execute("SELECT count(*) FROM memory_events").fetchone()[0] == 0
    assert selected.store.read(selected.key)["items"][0]["origin"] == "onboarding"
    client.post("/logout", headers=headers)


def test_default_admin_preview_has_no_empty_provider_call(
    console, welcome_enabled, monkeypatch
):
    client, headers, _ = console
    ordinary = Mock()
    monkeypatch.setattr(admin, "llm_answer", ordinary)
    response = client.post(
        "/test-preset",
        json={
            "preset_key": "main",
            "message": "",
            "memory_user": "",
            "welcome_start": True,
        },
        headers=headers,
    )
    assert response.json["ok"] and "profil nommé" in response.json["answer"]
    ordinary.assert_not_called()
    client.post("/logout", headers=headers)


def test_admin_birthday_is_an_answer_not_automatic_arithmetic(
    console, welcome_enabled, monkeypatch
):
    client, headers, _ = console
    model = Mock()
    model.client.complete.return_value = response()
    monkeypatch.setattr(sessions, "MemoryModel", lambda **kwargs: model)
    client.post(
        "/test-preset",
        json={
            "preset_key": "main",
            "message": "",
            "memory_user": "Birthday",
            "welcome_start": True,
        },
        headers=headers,
    )
    model.client.complete.return_value = response(
        reply="Quelle matière aimez-vous ?", topic="daily_life", covered=["age"]
    )
    result = client.post(
        "/test-preset",
        json={"preset_key": "main", "memory_user": "Birthday", "message": "14/08/2016"},
        headers=headers,
    )
    assert result.json["ok"] and "matière" in result.json["answer"]
    assert model.client.complete.call_args.args[1]["current_reply"] == "14/08/2016"
    assert len(model.client.complete.call_args.args[1]["today"]) == 10
    client.post("/logout", headers=headers)


def test_admin_continues_after_intro_with_real_http_adapter_and_imperfect_memory(
    console, welcome_enabled, monkeypatch
):
    client, headers, _ = console
    monkeypatch.setattr(admin, "mistral_key", lambda: "synthetic-key")

    def post(*args, **kwargs):
        import json
        data = json.loads(kwargs["json"]["messages"][1]["content"])
        assert data["memory_item_example"]["evidence"][0]["event_id"] == data["current_event_id"]
        if data["phase"] == "first":
            text = response(reply="Faisons connaissance. Qui es-tu dans la vie ?", topic="daily_life")
        elif data["current_reply"] == "J'ai 10 ans.":
            proposal = fact("J'ai 10 ans.", event_id=data["current_event_id"])
            del proposal["id"], proposal["title"]
            text = response(reply="Quelle matière aimes-tu ?", topic="daily_life", memories=[proposal])
        else:
            text = response(reply="Qu'est-ce qui te plaît dans le dessin ?", topic="interests",
                            memories=[fact("invented evidence", event_id=999)])
        result = Mock()
        result.json.return_value = {"choices": [{"finish_reason": "stop", "message": {"content": text}}]}
        return result

    monkeypatch.setattr(gmail_mail.requests, "post", post)
    for message, expected in (("", "connaissance"), ("J'ai 10 ans.", "matière"), ("Le dessin.", "dessin")):
        result = client.post("/test-preset", headers=headers, json={"preset_key": "main",
            "memory_user": "Flow test", "message": message, "welcome_start": not message})
        assert result.json["ok"] and expected in result.json["answer"]
    with client.session_transaction() as cookie:
        selected = admin.MEMORY_SESSIONS[cookie["memory_session"]]["memory"]
    assert selected.welcome.state()["status"] == "active"
    assert len(selected.store.read(selected.key)["items"]) == 1
    client.post("/logout", headers=headers)
