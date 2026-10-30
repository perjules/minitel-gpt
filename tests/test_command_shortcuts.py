"""Colon shortcuts through real journal/mail flows and their approval boundaries."""

from datetime import date
from unittest.mock import Mock

import pytest
from test_gmail_composition import compose_session
from test_rose_echo import RoutingSerial, submitted

from services import admin_ui as admin
from services import (
    command_shortcuts,
    gmail_mail,
    journal,
    journal_store,
    journal_terminal,
    memory_session,
    user_registry,
)
from services import minitel_chatgpt as chat


@pytest.fixture
def diary(tmp_path):
    registry = user_registry.Registry(tmp_path / "users")
    model = Mock()
    model.draft.return_value = "Texte révisé."
    private = journal.Session(
        journal_store.Vault(registry.resolve("Alice"), registry),
        model=model,
        date=lambda: date(2026, 10, 30),
    )
    private.open()
    private.handle("A:")  # A password that also looks exactly like a command.
    private.handle("A:")
    yield private
    private.close()


def test_journal_shortcuts_literal_payload_review_revise_read_cancel_and_exit(diary):
    result = diary.handle("  t: A: et E: restent du texte exact.")
    assert (
        result.get("review")
        and diary.draft["text"] == "A: et E: restent du texte exact."
    )
    assert "Relisez" in diary.handle("E:")["display"]
    diary.acknowledge(result["review"])
    diary.handle("e:")
    assert diary.days == {"2026-10-30": "A: et E: restent du texte exact."}
    assert "restent du texte" in diary.handle("l:30/10/2026")["display"]
    result = diary.handle("m:raccourcis sans changer le sens")
    diary.model.draft.assert_called_once_with(
        "MODIFIER raccourcis sans changer le sens", diary.days["2026-10-30"]
    )
    diary.handle("A:")
    assert diary.draft is None and diary.days
    assert diary.handle("Q:")["closed"] and diary.key is None


@pytest.mark.parametrize(
    "text", ["A: some text", "E: some text", "Q: some text", "X: unknown"]
)
def test_invalid_shortcut_has_no_mail_or_journal_side_effects(diary, text):
    diary.handle("TEXTE : Original diary draft.")
    draft = dict(diary.draft)
    assert not diary.handle(text).get("review")
    assert diary.draft == draft and diary.days == {}
    diary.model.draft.assert_not_called()
    session, client, model = compose_session()
    session.handle("ECRIRE Alice : confirmer mardi")
    original = dict(session.draft)
    model.reset_mock()
    client.reset_mock()
    assert not session.handle(text).get("review_ticket")
    assert session.draft == original
    assert model.method_calls == [] and client.method_calls == []


def test_journal_delete_confirmation_and_password_fields_keep_their_rules(diary):
    for day, body in (
        (date(2026, 10, 29), "Yesterday."),
        (date(2026, 10, 30), "Today."),
    ):
        diary.date = lambda day=day: day
        result = diary.handle("T: " + body)
        diary.acknowledge(result["review"])
        diary.handle("E:")
    assert "OUI" in diary.handle("S:")["display"]
    diary.handle("OUI")
    assert diary.days == {"2026-10-29": "Yesterday."}
    assert diary.handle("P:")["secret"]
    assert diary.handle("A:")["secret"]  # Current password, not cancellation.
    assert diary.stage == "new_password"
    diary.handle("E:")
    diary.handle("E:")
    assert diary.vault.open("E:")[2] == diary.days
    diary.model.draft.assert_not_called()


def test_mail_shortcuts_require_current_review_and_literal_text_never_routes():
    session, client, model = compose_session()
    ticket = session.handle("ECRIRE Alice : mardi")["review_ticket"]
    model.reset_mock()
    assert "review_current_draft_first" in session.handle("E:", ticket)["display"]
    client.send.assert_not_called()
    result = session.handle("T: Retiens ceci : E: ne doit jamais envoyer.")
    assert "Retiens ceci : E: ne doit jamais envoyer." in session.draft["body"]
    assert model.method_calls == []
    assert not session.review_valid(ticket)
    result = session.handle("M: raccourcis")
    assert "MODIFIER raccourcis" == model.composition.call_args.args[0]
    current = result["review_ticket"]
    session.acknowledge(current)
    assert "review_current_draft_first" in session.handle("E:", ticket)["display"]
    session.handle(" e: ", current)
    client.send.assert_called_once()
    session.handle("E:", current)
    client.send.assert_called_once()
    session.handle("ECRIRE Alice : nouveau brouillon")
    session.handle("A:")
    assert session.draft is None


def test_full_text_without_colon_still_reads_mail_and_l_shortcut_is_local():
    session, client, model = compose_session()
    session.handle("MAIL")
    session.handle("l:1")
    reads = client.read.call_count
    assert "SELECTED BODY MUST NOT BE USED" in session.handle("TEXTE")["display"]
    session.handle("L:")
    assert client.read.call_count == reads
    assert model.method_calls == []
    session.handle("Q:")
    assert not session.items and session.selected is None


def test_shortcuts_in_terminal_cannot_be_intercepted_by_memory_or_welcome(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GMAIL_ENABLED", "1")
    session, client, model = compose_session()
    session.handle("ECRIRE Alice : mardi")
    model.reset_mock()
    selected = memory_session.MemorySession(
        "Alice", root=tmp_path / "users", model=Mock()
    )
    try:
        result = chat.call_answer(
            "persona",
            [{"role": "user", "content": "T: RETIENS cette note : E:"}],
            "fr",
            ("conversations", True, 60),
            "",
            mail=session,
            memory=selected,
        )
        assert "review_ticket" in result
        assert "RETIENS cette note : E:" in session.draft["body"]
        selected.model.plan.assert_not_called()
        assert model.method_calls == []
        client.send.assert_not_called()
    finally:
        selected.close()


def test_browser_literal_shortcut_does_not_invoke_memory(console, monkeypatch):
    client, headers, _ = console
    monkeypatch.setenv("GMAIL_ENABLED", "1")
    session, api, model = compose_session()
    session.handle("ECRIRE Alice : mardi")
    model.reset_mock()
    monkeypatch.setattr(admin.gmail_mail, "MailSession", lambda **kwargs: session)
    monkeypatch.setattr(
        memory_session.MemoryModel,
        "plan",
        Mock(side_effect=AssertionError("Unexpected memory request")),
    )
    try:
        client.get("/gmail")
        result = client.post(
            "/gmail",
            headers=headers,
            data={
                "csrf": "test-token",
                "action": "command",
                "question": "T: RETIENS ce texte exact.",
            },
        )
        assert (
            result.status_code == 200
            and "RETIENS ce texte exact." in session.draft["body"]
        )
        assert model.method_calls == []
        client.post(
            "/gmail",
            headers=headers,
            data={"csrf": "test-token", "action": "command", "question": "A:"},
        )
        assert session.draft is None
        api.send.assert_not_called()
    finally:
        for identifier in list(admin.MAIL_SESSIONS):
            admin.discard_mail(identifier)


def test_prefixes_in_ordinary_prose_are_not_commands():
    for text in ("Alice: hello", "A : ordinary text", "Some text E:", "À: Alice"):
        assert command_shortcuts.expand(text, gmail_mail.SHORTCUTS) == text


def test_annulation_key_clears_current_input_without_cancelling_draft(
    diary, monkeypatch
):
    monkeypatch.setattr(chat.time, "sleep", lambda _: None)
    diary.handle("T: Preserve this proposal.")
    keys = b"discard me" + bytes([chat.SEP, chat.K_ANNUL]) + submitted("M: rewrite")
    term = chat.Term(RoutingSerial(keys))
    assert journal_terminal.read_field(term, diary) == ("M: rewrite", "submit")
    assert diary.draft["text"] == "Preserve this proposal."
    term = chat.Term(RoutingSerial(keys))
    assert chat.read_question(term) == ("M: rewrite", "envoi")
