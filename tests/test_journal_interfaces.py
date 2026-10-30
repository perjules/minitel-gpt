"""Real private console boundaries; no live Mistral, mail or hardware requests."""

import json
import re
import shutil
import subprocess
from collections import deque
from datetime import date
from pathlib import Path
from unittest.mock import Mock

import pytest
from test_rose_echo import RoutingSerial, submitted

from services import admin_ui as admin
from services import (
    journal,
    journal_store,
    journal_terminal,
    memory_session,
    user_registry,
)
from services import minitel_chatgpt as chat


@pytest.fixture(autouse=True)
def clear_private_sessions():
    yield
    for identifier in list(admin.JOURNAL_SESSIONS):
        admin.discard_journal(identifier)
    for identifier in list(admin.MEMORY_SESSIONS):
        admin.discard_memory(identifier)


def api(client, headers, action, **data):
    response = client.post(
        "/journal-api", headers=headers, json={"action": action, **data}
    )
    assert "no-store" in response.headers["Cache-Control"]
    return response


def create(client, headers, name="Alice"):
    result = api(client, headers, "open", username=name)
    while result.json.get("more"):
        result = api(client, headers, "next")
    assert result.json["secret"]
    api(client, headers, "command", text="k")
    result = api(client, headers, "command", text="k")
    while result.json.get("more"):
        result = api(client, headers, "next")
    assert not result.json["secret"]
    with client.session_transaction() as state:
        return admin.JOURNAL_SESSIONS[state["private_journal"]]


def test_browser_passwords_and_entries_never_enter_cookie_logs_or_memory(
    console, monkeypatch, caplog
):
    client, headers, _ = console
    no_network = Mock(side_effect=AssertionError("Unexpected API request"))
    monkeypatch.setattr(journal.requests, "post", no_network)
    private = create(client, headers)
    diary_text = "PRIVATE DIARY CONTENT ONLY IN CIPHERTEXT"
    result = api(client, headers, "command", text="TEXTE : " + diary_text)
    while result.json.get("more"):
        result = api(client, headers, "next")
    assert private.draft["reviewed"]
    api(client, headers, "command", text="ENREGISTRER")
    root = private.vault.registry.root
    for path in root.rglob("*"):
        if path.is_file():
            assert diary_text.encode() not in path.read_bytes()
    with client.session_transaction() as state:
        assert "key" not in state and "password" not in state
        assert diary_text not in json.dumps(dict(state))
    assert diary_text not in caplog.text
    assert not private.secret and private.key is not None
    assert api(client, headers, "lock").json["closed"]
    assert private.key is None and private.pages == []
    no_network.assert_not_called()


def test_browser_review_all_pages_before_save_and_profile_switch_closes(console):
    client, headers, _ = console
    private = create(client, headers)
    text = "TEXTE : " + "A private paragraph. " * 75
    result = api(client, headers, "command", text=text)
    assert result.json["more"]
    assert not private.draft["reviewed"]
    result = api(client, headers, "command", text="ENREGISTRER")
    assert "Relisez" in result.json["display"]
    while result.json.get("more"):
        result = api(client, headers, "next")
    assert private.draft["reviewed"]
    api(client, headers, "command", text="ENREGISTRER")
    client.post(
        "/test-preset",
        headers=headers,
        json={"preset_key": "main", "memory_user": "Bob", "reset": True},
    )
    assert private.key is None and private.pages == []


def test_browser_auth_csrf_transport_reopen_and_idle_expiry(console):
    client, headers, _ = console
    assert (
        admin.app.test_client()
        .post("/journal-api", json={"action": "open"})
        .status_code
        == 302
    )
    assert client.post("/journal-api", json={"action": "open"}).status_code == 403
    assert (
        client.post(
            "/journal-api",
            headers=headers,
            json={"action": "open", "username": "Alice"},
            environ_overrides={"REMOTE_ADDR": "192.0.2.44"},
        ).status_code
        == 403
    )
    private = create(client, headers)
    private.clock = lambda: private.deadline + 1
    private.expire(private.epoch)
    assert private.pages == [] and private.key is None
    assert api(client, headers, "command", text="LIRE").json["closed"]
    result = api(client, headers, "open", username="Alice")
    assert result.json["secret"]
    assert "incorrect" in api(client, headers, "command", text="not k").json["display"]
    assert api(client, headers, "command", text="k").json["closed"] is False
    with client.session_transaction() as state:
        unlocked = admin.JOURNAL_SESSIONS[state["private_journal"]]
    client.post("/logout", headers=headers)
    assert unlocked.key is None


def test_reading_another_screen_does_not_approve_an_unseen_draft(console):
    client, headers, _ = console
    private = create(client, headers)
    result = api(
        client, headers, "command", text="TEXTE : " + "Private long text. " * 80
    )
    assert result.json["more"] and not private.draft["reviewed"]
    api(client, headers, "command", text="LIRE HIER")
    assert not private.draft["reviewed"]
    assert (
        "Relisez" in api(client, headers, "command", text="ENREGISTRER").json["display"]
    )


def test_browser_journal_commands_on_first_page_and_previous_after_last(console):
    client, headers, _ = console
    private = create(client, headers)
    result = api(
        client, headers, "command", text="T: " + "A long private paragraph. " * 65
    )
    assert result.json["more"] and not private.draft["reviewed"]
    first = result.json["display"]
    api(client, headers, "next")
    result = api(client, headers, "previous")
    assert result.json["display"] == first and result.json["page"] == 1
    assert not private.draft["reviewed"]
    assert "Relisez" in api(client, headers, "command", text="E:").json["display"]
    while private.page < len(private.pages) - 1:
        api(client, headers, "next")
    assert private.draft["reviewed"]
    api(client, headers, "previous")
    assert "enregistrée" in api(client, headers, "command", text="E:").json["display"]
    assert private.days
    # Cancelling a different, unread proposal is also allowed on its first page.
    api(client, headers, "command", text="T: " + "Another paragraph. " * 80)
    api(client, headers, "command", text="A:")
    assert private.draft is None


def test_chat_keyword_routes_before_welcome_memory_or_ai(console, monkeypatch):
    client, headers, _ = console
    selected = Mock(side_effect=AssertionError("Journal must bypass conversation"))
    monkeypatch.setattr(admin, "memory_entry", selected)
    response = client.post(
        "/test-preset",
        headers=headers,
        json={
            "preset_key": "main",
            "memory_user": "Alice",
            "message": "JOURNAL INTIME",
        },
    )
    assert response.json["journal_url"] == "/journal"
    selected.assert_not_called()


def test_hardware_echo_password_hidden_normal_input_once_and_routes_restored(
    tmp_path, monkeypatch
):
    registry = user_registry.Registry(tmp_path / "users")
    user = registry.resolve("Alice")
    monkeypatch.setattr(chat.time, "sleep", lambda _: None)
    keys = (
        submitted("SYNTHETIC_PW_q7")
        + submitted("SYNTHETIC_PW_q7")
        + submitted("TEXTE : PRIVATE NOTE")
        + bytes([chat.SEP, chat.K_SOMMAIRE])
    )
    serial = RoutingSerial(keys)
    term = chat.Term(serial)
    routes = dict(serial.routes)
    # A real user waits for the result before typing the next command. The
    # synthetic serial queues future human input; don't consume it as typeahead.
    monkeypatch.setattr(
        journal_terminal, "wait", lambda t, s, p: (p[1].get(timeout=2), "done")
    )
    action, _ = journal_terminal.run(term, user, registry, model=Mock())
    assert action == "sommaire" and serial.routes == routes
    assert all(copies == 0 for _, copies in serial.echo)
    assert b"SYNTHETIC_PW_q7" not in serial.output
    assert serial.output.endswith(b"Journal verrouille.\r\n")
    # The typed text is explicitly echoed once; it appears again in its preview.
    assert serial.output.count(b"TEXTE : PRIVATE NOTE") == 1
    assert serial.output.count(b"PRIVATE NOTE") == 2
    assert not (registry.root / user.memory_key / "logs").exists()


def test_private_reader_timeout_clears_password_and_screen(tmp_path, monkeypatch):
    registry = user_registry.Registry(tmp_path / "users")
    user = registry.resolve("Alice")
    serial = RoutingSerial()
    term = chat.Term(serial)
    monkeypatch.setattr(chat.time, "sleep", lambda _: None)
    real_session = journal.Session
    instances = []

    def make(*args):
        session = real_session(*args)
        instances.append(session)
        return session

    monkeypatch.setattr(journal_terminal.journal, "Session", make)

    def idle(_):
        session = instances[0]
        session.deadline = session.clock() - 1
        return "timeout", None

    monkeypatch.setattr(term, "read_key", idle)
    assert journal_terminal.run(term, user, registry)[0] == "retour"
    assert instances[0].key is None
    assert serial.output.endswith(b"Journal verrouille.\r\n")


def test_late_terminal_model_result_is_abandoned_on_navigation(tmp_path, monkeypatch):
    registry = user_registry.Registry(tmp_path / "users")
    private = journal.Session(journal_store.Vault(registry.resolve("Alice"), registry))
    try:
        private.open()
        private.handle("x")
        private.handle("x")
        # Queue a completed private result: navigation must still win.
        from queue import Queue

        output = Queue()
        output.put({"display": "PRIVATE LATE RESULT"})
        pending = Mock(), output, journal_terminal.time.monotonic()
        term = Mock()
        term.read_key.return_value = "fn", chat.K_SOMMAIRE
        result, action = journal_terminal.wait(term, private, pending)
        assert action == "sommaire" and result is None and private.key is None
        term.w.assert_not_called()
    finally:
        private.close()


def test_external_helper_edit_history_and_change_password(tmp_path, monkeypatch):
    from scripts import journal as helper

    registry = user_registry.Registry(tmp_path / "users")
    vault = journal_store.Vault(registry.resolve("Alice"), registry)
    private = journal.Session(vault, date=lambda: date(2026, 10, 30))
    try:
        private.open()
        private.handle("x")
        private.handle("x")
        path = registry.root / vault.user.memory_key / journal_store.FILENAME
        plaintext = tmp_path / "edited.md"
        monkeypatch.setattr(helper.getpass, "getpass", lambda _: "x")
        assert helper.main(["export", str(path), "--output", str(plaintext)]) == 0
        plaintext.write_text(
            "# Journal intime\n\n## 2000-01-01\n\nExternally edited history.\n",
            encoding="utf-8",
        )
        assert helper.main(["import", str(path), "--input", str(plaintext)]) == 0
        assert vault.open("x")[2] == {"2000-01-01": "Externally edited history."}
        passwords = deque(["x", "z", "z"])
        monkeypatch.setattr(helper.getpass, "getpass", lambda _: passwords.popleft())
        assert helper.main(["password", str(path)]) == 0
        assert vault.open("z")[2] == {"2000-01-01": "Externally edited history."}
        assert b"Externally edited" not in path.read_bytes()
    finally:
        private.close()


def test_console_javascript_hides_input_locks_and_discards_late_results(console):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for browser script checks")
    client, _, _ = console
    page = client.get("/journal")
    script = re.search(r"<script>(.*?)</script>", page.text, re.DOTALL)[1]
    result = subprocess.run(
        [node, str(Path(__file__).with_name("journal_console.js"))],
        input=json.dumps({"script": script}),
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_full_uart_loop_never_records_private_input(tmp_path, monkeypatch):
    class EndOfInput(Exception):
        pass

    class ScriptedSerial(RoutingSerial):
        def read(self, count):
            if not self.incoming and not self.keys:
                raise EndOfInput
            return super().read(count)

    selected = memory_session.MemorySession("Alice", root=tmp_path / "users")
    selected.remember_language("fr")
    secret_text = "PRIVATE SYNTHETIC DIARY NOTES"
    keys = b"".join(
        submitted(text)
        for text in (
            "JOURNAL INTIME",
            "q",
            "q",
            "TEXTE : " + secret_text,
            "ENREGISTRER",
            "QUITTER",
            "Ordinary synthetic greeting.",
        )
    )
    serial = ScriptedSerial(keys)
    term = chat.Term(serial)
    monkeypatch.setattr(chat.time, "sleep", lambda _: None)
    monkeypatch.setattr(chat, "wait_connection", lambda _: None)
    monkeypatch.setattr(chat, "prepare_boot", lambda _: [])
    monkeypatch.setattr(chat, "show_boot", lambda *args: "done")
    monkeypatch.setattr(
        chat, "select_memory_user", lambda *args, **kwargs: (selected, "envoi")
    )
    monkeypatch.setattr(
        journal_terminal, "wait", lambda t, s, p: (p[1].get(timeout=2), "done")
    )
    calls = []

    def start(system, history, *args, **kwargs):
        from queue import Queue

        calls.append(history)
        output = Queue()
        output.put(
            (
                {
                    "display": "Ordinary answer.",
                    "turn": {"role": "assistant", "content": "Ordinary answer."},
                },
                None,
            )
        )
        return Mock(is_alive=lambda: False), output, chat.time.monotonic()

    monkeypatch.setattr(chat, "start_answer", start)
    monkeypatch.setattr(
        chat, "wait_answer", lambda t, p, *args, **kwargs: (p[1].get()[0], "done")
    )
    with pytest.raises(EndOfInput):
        chat.run(term)
    assert len(calls) == 1
    assert calls[0] == [{"role": "user", "content": "Ordinary synthetic greeting."}]
    for path in selected.store.root.rglob("*"):
        if path.is_file():
            assert secret_text.encode() not in path.read_bytes()
    vault = journal_store.Vault(selected.user, selected.registry)
    assert vault.open("q")[2] == {journal.today().isoformat(): secret_text}
