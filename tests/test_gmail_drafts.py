"""Local recovery with real profile grants and synthetic Gmail/model calls only."""

import json
import threading
from email import policy
from email.parser import BytesParser
from unittest.mock import Mock

import pytest
from test_gmail import draft, message

from services import admin_ui as admin
from services import (
    gmail_auth,
    gmail_client,
    gmail_drafts,
    gmail_mail,
    user_registry,
    user_services,
)
from services import minitel_chatgpt as chat

ACCOUNT = "a" * 32
OTHER = "b" * 32


@pytest.fixture
def mailbox(monkeypatch):
    monkeypatch.setenv("GMAIL_ENABLED", "1")
    registry = user_registry.Registry()
    registry.record_account(ACCOUNT, "minitel@example.org", 1)
    registry.record_account(OTHER, "other@example.org", 1)
    registry.configure(1, "MinitelGPT", True, [], ACCOUNT, True, [])

    def session(user_id=1):
        client = user_services.ProfileMailClient(registry.get(user_id), registry)
        account = registry.gmail(user_id)[1]
        client.tokens.email_address = "minitel@example.org" if account == ACCOUNT else "other@example.org"
        client.tokens.token = Mock(return_value="synthetic-token")
        client.list = Mock(return_value=[gmail_client.message_record(message())])
        client.read = Mock(return_value=gmail_client.message_record(message(text="HISTOIRE ORIGINALE"), full=True))
        client.send = Mock(return_value="sent")
        model = Mock()
        model.complete.return_value = "Merci pour le café."
        model.composition.return_value = ("Bonjour", "Un nouveau message.")
        model.route.return_value = {"action": "revise"}
        return gmail_mail.MailSession(client, model, username=registry.get(user_id).display_name)

    return registry, session


def files(registry):
    return list(registry.root.rglob("mail-draft-*.json"))


@pytest.mark.parametrize("reply", [False, True])
def test_restart_restores_exact_draft_and_reply_context_with_fresh_approval(mailbox, reply):
    registry, new = mailbox
    first = new()
    old_ticket = (draft(first) if reply else
                  first.handle("ECRIRE owner@example.org : bonjour")["review_ticket"])
    original = dict(first.draft)
    first.acknowledge(old_ticket)
    first.reset()
    recovered = new()
    assert recovered.interested("BROUILLON") and recovered.interested("B:")
    result = recovered.handle("B:")
    ticket = result["review_ticket"]
    assert ticket != old_ticket
    assert not recovered.review_valid(ticket)
    assert recovered.draft["raw"] == original["raw"]
    assert recovered.draft["body"] == original["body"]
    assert recovered.draft["reply_source"] == original["reply_source"]
    recovered.model.complete.assert_not_called()
    recovered.model.composition.assert_not_called()
    recovered.client.read.assert_not_called()
    recovered.client.tokens.token.assert_not_called()
    recovered.handle("E:", old_ticket)
    recovered.handle("E:", ticket)
    recovered.client.send.assert_not_called()
    recovered.acknowledge(ticket)
    recovered.handle("E:", ticket)
    recovered.client.send.assert_called_once()
    sent = BytesParser(policy=policy.default).parsebytes(recovered.client.send.call_args.args[0])
    assert str(sent["To"]) == original["recipient"]
    assert str(sent["Message-ID"]) == original["message_id"]
    if reply:
        assert sent["In-Reply-To"] == "<original@example.org>"
        assert "> HISTOIRE ORIGINALE" in sent.get_content()
        assert recovered.client.send.call_args.args[1] == "thread123"
    else:
        assert sent["In-Reply-To"] is None
        assert recovered.client.send.call_args.args[1] is None
    assert not files(registry)
    assert "Aucun brouillon" in new().handle("BROUILLON")["display"]


def test_each_revision_is_saved_and_failed_generation_preserves_last_good_version(mailbox):
    registry, new = mailbox
    session = new()
    draft(session)
    session.handle("T: Une dernière précision.")
    previous = session.draft["body"]
    session.model.complete.side_effect = gmail_auth.GmailError("generation_unavailable")
    session.handle("M: raccourcis le message")
    restored = new()
    restored.handle("B:")
    assert restored.draft["body"] == previous
    assert "Une dernière précision." in previous
    saved = files(registry)[0]
    data = saved.read_text()
    assert "Une dernière précision." in data
    assert saved.stat().st_mode & 0o777 == 0o600
    assert "synthetic-token" not in data
    assert not {"ticket", "reviewed", "status"} & json.loads(data)["draft"].keys()
    assert set(json.loads(data)["selected"]["raw"]["payload"]) == {"headers"}
    restored.handle("T: Une autre précision.")
    assert len(files(registry)) == 1


@pytest.mark.parametrize("command", ["BROUILLON", "B:"])
def test_recovery_routes_directly_from_the_main_prompt_before_the_welcome_flow(mailbox, command):
    _, new = mailbox
    draft(new())
    memory = Mock()
    memory.welcome = Mock(spec=chat.memory_session.onboarding.Session)
    response = chat.call_answer("prompt", [{"role": "user", "content": command}], "fr",
                                ("conversations", False, 60), {}, mail=new(), memory=memory)
    assert response["gmail"] and response["review_ticket"]
    assert "Merci pour le café." in response["display"]
    memory.welcome.respond.assert_not_called()
    memory.handle.assert_not_called()


@pytest.mark.parametrize("command", ["SOMMAIRE", "QUITTER", "Q:"])
def test_leaving_preserves_but_explicit_cancel_deletes(mailbox, command):
    registry, new = mailbox
    session = new()
    draft(session)
    session.handle(command)
    assert session.draft is None and len(files(registry)) == 1
    recovered = new()
    recovered.handle("B:")
    recovered.handle("A:")
    assert not files(registry)


def test_profiles_sharing_an_account_and_different_account_assignments_are_isolated(mailbox):
    registry, new = mailbox
    alice = registry.resolve("Alice")
    registry.configure(alice.id, "Alice", True, [], ACCOUNT, True, [])
    default, personal = new(), new(alice.id)
    draft(default)
    draft(personal)
    personal.handle("T: PRIVÉ ALICE")
    default.handle("B:")
    assert "PRIVÉ ALICE" not in default.draft["body"]
    assert len(files(registry)) == 2
    registry.configure(1, "MinitelGPT", True, [], OTHER, True, [])
    assert "Aucun brouillon" in new().handle("B:")["display"]
    assert "account_assignment_changed" in default.handle("B:")["display"]
    draft(new())
    assert len(files(registry)) == 3
    registry.configure(1, "MinitelGPT", True, [], ACCOUNT, True, [])
    restored = new()
    restored.handle("B:")
    assert restored.draft["recipient"] == "reply@example.org"
    registry.delete(alice.id, registry.get(alice.id).revision, "Alice")
    assert len(files(registry)) == 2
    assert "user_disabled" in personal.handle("B:")["display"]


def test_stale_browser_cannot_overwrite_send_or_delete_a_newer_terminal_revision(mailbox):
    registry, new = mailbox
    terminal = new()
    draft(terminal)
    browser = new()
    old = browser.handle("B:")["review_ticket"]
    browser.acknowledge(old)
    terminal.handle("T: NOUVELLE VERSION")
    assert "a changé" in browser.handle("E:", old)["display"]
    browser.client.send.assert_not_called()
    assert "saved_draft_changed" in browser.handle("A:")["display"]
    assert "a changé" in browser.handle("T: ajout périmé")["display"]
    recovered = new()
    recovered.handle("B:")
    assert "NOUVELLE VERSION" in recovered.draft["body"]
    assert "ajout périmé" not in recovered.draft["body"]
    assert len(files(registry)) == 1


@pytest.mark.parametrize("failure", ["uncertain", "crash"])
def test_send_attempt_is_durable_before_http_and_never_resubmitted_after_restart(mailbox, failure):
    registry, new = mailbox
    session = new()
    ticket = draft(session)
    session.acknowledge(ticket)

    def fail(*args):
        assert json.loads(files(registry)[0].read_text())["state"] == "sending"
        if failure == "crash":
            raise RuntimeError("synthetic process death")
        raise gmail_auth.GmailError("send_uncertain")

    session.client.send.side_effect = fail
    if failure == "crash":
        with pytest.raises(RuntimeError):
            session.handle("E:", ticket)
    else:
        assert "Envoi incertain" in session.handle("E:", ticket)["display"]
    recovered = new()
    result = recovered.handle("B:")
    assert "ENVOI INCERTAIN" in result["display"]
    assert "Merci pour le café." in result["display"]
    assert "review_ticket" not in result
    for command in ("E:", "M: nouvelle version", "T: changer"):
        recovered.handle(command, ticket)
    recovered.client.send.assert_not_called()
    recovered.reset()
    assert "tentative" in recovered.handle("ECRIRE owner@example.org : nouveau")["display"]
    assert json.loads(files(registry)[0].read_text())["state"] == "sending"
    recovered.handle("A:")
    assert not files(registry)


def test_failed_atomic_save_preserves_previous_file_and_prevents_sending(mailbox, monkeypatch):
    registry, new = mailbox
    session = new()
    ticket = draft(session)
    original = files(registry)[0].read_bytes()
    monkeypatch.setattr(gmail_drafts.os, "replace", Mock(side_effect=OSError("synthetic disk failure")))
    assert "Sauvegarde locale indisponible" in session.handle("T: nouvelle révision")["display"]
    assert files(registry)[0].read_bytes() == original
    session.acknowledge(session.draft["ticket"])
    assert "Sauvegarde locale indisponible" in session.handle("E:", session.draft["ticket"])["display"]
    session.client.send.assert_not_called()
    assert not list(registry.root.rglob(".mail-draft-*"))
    assert not session.review_valid(ticket)


def test_damaged_and_symlinked_storage_is_not_loaded_or_overwritten(mailbox, tmp_path):
    registry, new = mailbox
    draft(new())
    path = files(registry)[0]
    path.write_text('{"unfinished":')
    session = new()
    assert "illisible" in session.handle("B:")["display"]
    assert path.read_text() == '{"unfinished":'
    session.handle("A:")
    assert not path.exists()
    target = tmp_path / "unrelated.txt"
    target.write_text("PRIVATE OUTSIDE CONTENT")
    path.symlink_to(target)
    assert "Sauvegarde locale indisponible" in session.handle("B:")["display"]
    assert target.read_text() == "PRIVATE OUTSIDE CONTENT"
    session.handle("A:")
    assert not path.is_symlink() and target.exists()


def test_cancel_during_generation_prevents_late_persistence(mailbox):
    registry, new = mailbox
    session = new()
    draft(session)
    started, release = threading.Event(), threading.Event()

    def generate(*args, **kwargs):
        started.set()
        assert release.wait(3)
        return "LATE PRIVATE TEXT"

    session.model.complete.side_effect = generate
    worker = threading.Thread(target=session.handle, args=("M: plus court",))
    worker.start()
    assert started.wait(3)
    try:
        session.handle("A:")
    finally:
        release.set()
        worker.join(3)
    assert not worker.is_alive() and not files(registry)


def test_mismatched_saved_text_cannot_be_restored_and_failed_send_cleanup_stays_blocked(mailbox, monkeypatch):
    registry, new = mailbox
    session = new()
    ticket = draft(session)
    path = files(registry)[0]
    data = json.loads(path.read_text())
    data["draft"]["body"] = "DIFFERENT FROM STORED MIME"
    path.write_text(json.dumps(data))
    assert "illisible" in new().handle("B:")["display"]
    assert path.exists()
    session.handle("A:")
    ticket = draft(session)
    session.acknowledge(ticket)
    monkeypatch.setattr(session.draft_store, "delete", Mock(side_effect=gmail_auth.GmailError("disk_unavailable")))
    result = session.handle("E:", ticket)
    assert "Gmail confirme l'envoi" in result["display"]
    session.client.send.assert_called_once()
    restored = new()
    assert "ENVOI INCERTAIN" in restored.handle("B:")["display"]
    restored.handle("E:", ticket)
    restored.client.send.assert_not_called()


def test_admin_reset_preserves_and_cancel_removes_the_same_local_slot(mailbox, console, monkeypatch):
    registry, new = mailbox
    session = new()
    draft(session)
    client, _, _ = console
    item = {"mail": session, "lock": threading.Lock(), "pages": ["test"], "page": 0, "ticket": None}
    monkeypatch.setattr(admin, "mail_entry", lambda *args: item)
    client.get("/gmail")
    with client.session_transaction() as state:
        csrf = state["gmail_csrf"]
    assert client.post("/gmail", data={"csrf": csrf, "action": "reset"}).status_code == 200
    assert len(files(registry)) == 1
    response = client.post("/gmail", data={"csrf": csrf, "action": "command", "question": "B:"})
    assert response.status_code == 200 and session.draft
    assert client.post("/gmail", data={"csrf": csrf, "action": "cancel"}).status_code == 200
    assert not files(registry)
