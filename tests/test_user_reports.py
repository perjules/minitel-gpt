"""Synthetic lifecycle reports: no OAuth, Gmail requests or real background sends."""

import logging
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from email import policy
from email.parser import BytesParser
from queue import Queue
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from services import admin_ui as admin
from services import (
    gmail_auth,
    memory_session,
    user_registry,
    user_reports,
    user_services,
)

ACCOUNT = "a" * 32


@pytest.fixture
def reports(monkeypatch):
    monkeypatch.setenv("GMAIL_ENABLED", "1")
    monkeypatch.setattr(user_reports, "PENDING", Queue(maxsize=32))
    monkeypatch.setattr(user_reports, "WORKER", Mock(is_alive=lambda: True))
    registry = user_registry.Registry()
    registry.record_account(ACCOUNT, "minitel@example.org", 1)
    registry.configure(1, "MinitelGPT", True, [], ACCOUNT, True, [])
    registry.set_report_recipient("reports@web.de")
    return registry


def test_only_committed_new_identities_and_successful_deletions_produce_reports(reports, monkeypatch):
    user = reports.resolve("Alice")
    assert reports.resolve(" ALICE ").id == user.id
    assert user_registry.Registry().resolve("alice").id == user.id
    reports.resolve("")
    reports.configure(user.id, "Alicia", True, ["Alice"], None, False, [])
    with pytest.raises(gmail_auth.GmailError):
        reports.delete(user.id, reports.get(user.id).revision, "wrong confirmation")
    assert user_reports.PENDING.qsize() == 1
    real_remove = shutil.rmtree
    monkeypatch.setattr(shutil, "rmtree", Mock(side_effect=OSError("synthetic failure")))
    with pytest.raises(memory_session.memory.MemoryProblem):
        reports.delete(user.id, reports.get(user.id).revision, "Alicia")
    assert user_reports.PENDING.qsize() == 1
    monkeypatch.setattr(shutil, "rmtree", real_remove)
    details = next(u for u in reports.users() if u["id"] == user.id)
    reports.delete(user.id, details["revision"], "Alicia")
    events = [user_reports.PENDING.get_nowait(), user_reports.PENDING.get_nowait()]
    assert [(e["action"], e["id"], e["name"]) for e in events] == [
        ("created", user.id, "Alice"), ("deleted", user.id, "Alicia")]
    assert not any(u["id"] == user.id for u in reports.users())
    assert not (reports.root / user.memory_key).exists()


def test_concurrent_first_use_schedules_one_report_after_commit(reports):
    with ThreadPoolExecutor(max_workers=4) as executor:
        users = list(executor.map(lambda _: reports.resolve("Concurrent"), range(8)))
    assert len({u.id for u in users}) == 1
    assert user_reports.PENDING.qsize() == 1
    event = user_reports.PENDING.get_nowait()
    assert user_registry.Registry(reports.root).get(event["id"]).display_name == "Concurrent"


@pytest.mark.parametrize("missing", ["integration", "account", "send", "recipient"])
def test_reports_need_enabled_gmail_default_sender_and_explicit_recipient(reports, monkeypatch, missing):
    if missing == "integration":
        monkeypatch.setenv("GMAIL_ENABLED", "0")
    elif missing == "account":
        reports.configure(1, "MinitelGPT", True, [], None, False, [])
    elif missing == "send":
        reports.configure(1, "MinitelGPT", True, [], ACCOUNT, False, [])
    else:
        reports.set_report_recipient("")
    reports.resolve("New")
    assert user_reports.PENDING.empty()


@pytest.mark.parametrize("action", ["created", "deleted"])
def test_report_uses_default_account_and_arbitrary_unconnected_destination(reports, monkeypatch, action):
    user = reports.resolve("Élodie")
    event = user_reports.PENDING.get_nowait()
    if action == "deleted":
        reports.delete(user.id, user.revision, user.display_name)
        event = user_reports.PENDING.get_nowait()
    original = user_services.ProfileMailClient
    clients = []

    def client_for(user, registry):
        client = original(user, registry)
        client.send = Mock(return_value="sent")
        clients.append(client)
        return client

    monkeypatch.setattr(user_services, "ProfileMailClient", client_for)
    user_reports.send_report(event)
    client = clients[0]
    assert client.tokens.user.id == 1 and client.tokens.binding[1] == ACCOUNT
    raw, thread, identifier, deadline = client.send.call_args.args
    message = BytesParser(policy=policy.default).parsebytes(raw)
    assert message["From"] == "minitel@example.org"
    assert message["To"] == "reports@web.de"
    assert message["Message-ID"] == identifier
    assert message["Auto-Submitted"] == "auto-generated"
    assert thread is None and deadline > time.monotonic()
    assert ("créé" if action == "created" else "supprimé") in message["Subject"]
    text = message.get_content()
    assert "Élodie" in text and str(user.id) in text and "Europe/Paris" in text
    assert "notes" not in text and "mot de passe" not in text
    assert [a["email"] for a in reports.accounts()] == ["minitel@example.org"]


@pytest.mark.parametrize("change", ["recipient", "disabled", "sender"])
def test_pending_reports_recheck_destination_activation_and_sender_grants(reports, monkeypatch, change):
    reports.resolve("New")
    event = user_reports.PENDING.get_nowait()
    if change == "recipient":
        reports.set_report_recipient("another@hotmail.com")
    elif change == "disabled":
        monkeypatch.setenv("GMAIL_ENABLED", "0")
    else:
        reports.configure(1, "MinitelGPT", True, [], None, False, [])
    factory = Mock(side_effect=AssertionError("Network client should not be constructed"))
    if change == "sender":
        # Construct the real client so its immutable profile/account guard is exercised.
        with pytest.raises(gmail_auth.GmailError):
            user_reports.send_report(event)
    else:
        monkeypatch.setattr(user_services, "ProfileMailClient", factory)
        user_reports.send_report(event)
        factory.assert_not_called()


def test_reporting_failure_never_rolls_back_user_creation_or_deletion(reports, monkeypatch):
    failure = Mock(side_effect=RuntimeError("synthetic notification failure"))
    monkeypatch.setattr(user_reports, "enqueue", failure)
    created = reports.resolve("Still created")
    assert reports.get(created.id).display_name == "Still created"
    reports.delete(created.id, created.revision, created.display_name)
    assert len(reports.users()) == 1


def test_one_background_worker_and_bounded_queue_do_not_block_user_creation(reports, monkeypatch, caplog):
    worker = Mock(is_alive=lambda: True)
    factory = Mock(return_value=worker)
    monkeypatch.setattr(user_reports, "WORKER", None)
    monkeypatch.setattr(user_reports, "threading", SimpleNamespace(Thread=factory))
    monkeypatch.setattr(user_reports, "PENDING", Queue(maxsize=1))
    reports.resolve("First")
    with caplog.at_level(logging.WARNING):
        second = reports.resolve("Second")
    assert reports.get(second.id).display_name == "Second"
    assert user_reports.PENDING.qsize() == 1
    factory.assert_called_once()
    worker.start.assert_called_once()
    assert factory.call_args.kwargs["daemon"] is True
    assert "status=queue_full" in caplog.text


def test_worker_retires_uncertain_send_without_retry_and_continues(monkeypatch, caplog):
    class StopWorker(Exception):
        pass

    pending = Mock()
    pending.get.side_effect = [
        {"action": "created", "id": 2, "name": "PRIVATE NAME"},
        {"action": "deleted", "id": 2, "name": "PRIVATE NAME"}, StopWorker,
    ]
    send = Mock(side_effect=[gmail_auth.GmailError("send_uncertain"), None])
    monkeypatch.setattr(user_reports, "PENDING", pending)
    monkeypatch.setattr(user_reports, "send_report", send)
    with caplog.at_level(logging.WARNING), pytest.raises(StopWorker):
        user_reports.work()
    assert send.call_count == 2 and pending.task_done.call_count == 2
    assert "status=failed" in caplog.text and "PRIVATE NAME" not in caplog.text


def test_admin_report_address_is_protected_persistent_and_needs_no_connection(console, reports, monkeypatch):
    client, headers, _ = console
    helper = Mock(side_effect=AssertionError("Saving a recipient must not call Google or read tokens"))
    monkeypatch.setattr(gmail_auth, "credential_helper", helper)
    form = {"action": "reports", "report_recipient": "owner@another-company.example"}
    assert client.post("/services/gmail", data=form).status_code == 403
    assert admin.app.test_client().post("/services/gmail", data=form).status_code in (302, 403)
    response = client.post("/services/gmail", headers=headers, data=form)
    assert response.status_code == 200
    assert user_registry.Registry().report_recipient() == form["report_recipient"]
    assert "Rapports automatiques" in response.text
    assert "owner@another-company.example" in response.text
    helper.assert_not_called()
    for invalid in ("bad@@example.org", "a@example.org, b@example.org", "Name <a@example.org>", "a@example.org\r\nBcc: bad@example.org"):
        response = client.post("/services/gmail", headers=headers, data={"action": "reports", "report_recipient": invalid})
        assert response.status_code == 400
        assert reports.report_recipient() == form["report_recipient"]
    monkeypatch.setenv("GMAIL_ENABLED", "0")
    page = client.get("/services/gmail").text
    assert "Rapports suspendus" in page and 'type=email name=report_recipient' not in page
    assert client.post("/services/gmail", headers=headers, data={"action": "reports", "report_recipient": ""}).status_code == 200
    assert reports.report_recipient() == ""


def test_terminal_profile_creation_and_admin_deletion_use_the_same_report_hooks(console, reports):
    client, headers, _ = console
    selected = memory_session.MemorySession("Terminal user", model=Mock())
    user = selected.user
    selected.close()
    assert user_reports.PENDING.get_nowait()["action"] == "created"
    response = client.post(f"/users/{user.id}/delete", headers=headers,
                          data={"revision": user.revision, "confirmation": "Terminal user"})
    assert response.status_code == 302
    event = user_reports.PENDING.get_nowait()
    assert event["action"] == "deleted" and event["id"] == user.id
    response = client.post("/users", headers=headers, data={"action": "create", "name": "Admin user"})
    assert response.status_code == 200
    assert user_reports.PENDING.get_nowait()["name"] == "Admin user"
    assert user_reports.PENDING.empty()
