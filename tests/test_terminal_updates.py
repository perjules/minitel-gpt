"""Offline update preflight, theatrical boundaries and physical confirmation."""
import json
import subprocess
from queue import Queue
from unittest.mock import Mock, patch

import pytest
import test_terminal_startup as startup
from test_terminal_startup import fn, login_keys

from services import deployment
from services import mainframe_update as update
from services import minitel_chatgpt as chat


def test_real_local_remote_preview_uses_installed_branch_and_preserves_checkout(tmp_path):
    remote, checkout = tmp_path / "remote", tmp_path / "checkout"

    def git(path, *args):
        return subprocess.run(["git", "-C", str(path), *args], check=True,
                              capture_output=True, text=True).stdout.strip()

    remote.mkdir()
    git(remote, "init", "-b", "develop")
    git(remote, "config", "user.name", "Synthetic")
    git(remote, "config", "user.email", "test@example.invalid")
    (remote / "test.txt").write_text("first")
    git(remote, "add", ".")
    git(remote, "commit", "-m", "Initial")
    git(tmp_path, "clone", str(remote), str(checkout))
    head = git(checkout, "rev-parse", "HEAD")
    runtime, state = tmp_path / "runtime.json", tmp_path / "deployment.json"
    runtime.write_text(json.dumps({"branch": "develop", "active": {"commit": head}}))
    state.write_text('{"phase":"ready"}')
    assert deployment.available(checkout, runtime) is False
    (remote / "test.txt").write_text("second")
    git(remote, "commit", "-am", "Better messages")
    assert deployment.available(checkout, runtime) is True
    preview = deployment.preview(checkout, runtime, state)
    assert preview == {"ok": True, "behind": 1, "log": "Better messages", "head": head,
                       "branch": "develop", "target": git(remote, "rev-parse", "HEAD"),
                       "commits": [{"id": git(remote, "rev-parse", "HEAD"), "subject": "Better messages"}]}
    assert git(checkout, "rev-parse", "HEAD") == head
    assert (checkout / "test.txt").read_text() == "first"
    # A real tracked edit is refused; no stash/reset/checkout is attempted.
    (checkout / "test.txt").write_text("owner edit")
    assert not deployment.preview(checkout, runtime, state)["ok"]
    assert (checkout / "test.txt").read_text() == "owner edit"


@pytest.mark.parametrize("failure", ["missing", "branch", "head", "dirty", "fetch", "diverged", "busy"])
def test_failed_preflight_never_returns_changes_to_the_model(tmp_path, monkeypatch, failure):
    head, target = "a" * 40, "b" * 40
    runtime, status = tmp_path / "runtime.json", tmp_path / "status.json"
    if failure != "missing":
        runtime.write_text(json.dumps({"branch": "develop", "active": {"commit": head}}))
    status.write_text(json.dumps({"phase": "preparing" if failure == "busy" else "ready"}))

    def git(project, *args, **kwargs):
        if args[0] == "branch":
            return 0, "main" if failure == "branch" else "develop", ""
        if args[:2] == ("rev-parse", "HEAD"):
            return 0, target if failure == "head" else head, ""
        if args[0] == "status":
            return 0, " M test" if failure == "dirty" else "", ""
        if args[0] == "fetch":
            return (1 if failure == "fetch" else 0), "", "private remote diagnostics"
        if args[0] == "rev-list":
            return 0, "1", ""
        if args[0] == "log":
            return 0, target + "\0Synthetic change", ""
        if args[0] == "rev-parse":
            return 0, target, ""
        return (1 if failure == "diverged" else 0), "", ""

    run = Mock(side_effect=git)
    monkeypatch.setattr(deployment, "git", run)
    result = deployment.preview(tmp_path, runtime, status)
    assert result["ok"] is False and "log" not in result
    if failure in ("missing", "busy"):
        run.assert_not_called()


def test_git_timeout_kills_transport_group(monkeypatch, tmp_path):
    process = Mock(pid=123, returncode=1)
    process.communicate.side_effect = [subprocess.TimeoutExpired("git", 5), ("", "")]
    spawn, kill = Mock(return_value=process), Mock()
    monkeypatch.setattr(deployment.subprocess, "Popen", spawn)
    monkeypatch.setattr(deployment.os, "killpg", kill)
    assert deployment.git(tmp_path, "fetch", "origin", "develop", timeout=5)[0] == 1
    kill.assert_called_once_with(123, deployment.signal.SIGKILL)
    assert spawn.call_args.kwargs["start_new_session"] is True


def test_shared_queue_only_launches_fixed_helper(monkeypatch):
    run = Mock(return_value=Mock(returncode=0))
    monkeypatch.setattr(deployment.subprocess, "run", run)
    assert deployment.queue_deployment("update")[0]
    assert run.call_args.args[0] == ["sudo", "-n", "/usr/local/sbin/minitel-runtime", "update"]
    assert run.call_args.kwargs["timeout"] == 15
    assert not deployment.queue_deployment("apply-update")[0]
    assert run.call_count == 1


@pytest.mark.parametrize("text", ["UPDATE", " update ", "MISE A JOUR", "mise à jour", "MAJ"])
def test_exact_commands(text):
    assert update.command(text)
    for ordinary in ("Tell me about UPDATE", "updates", "MAJ mon mail", "update; reboot"):
        assert not update.command(ordinary)


def checked(behind=2):
    return {"ok": True, "behind": behind, "log": "Clearer mail\nBetter recall" if behind else "",
            "commits": [{"id": "c" * 40, "subject": "Clearer mail"},
                        {"id": "d" * 40, "subject": "Better recall"}] if behind else [],
            "branch": "develop", "head": "a" * 40, "target": "b" * 40}


@pytest.mark.parametrize("provider", ["mistral", "claude"])
def test_text_generation_is_separate_and_only_receives_bounded_change_subjects(monkeypatch, provider):
    generate = Mock(return_value="ALCATEL MF-83\nVisa du bureau 83 obtenu.")
    monkeypatch.setattr(chat, "PROVIDER", provider)
    monkeypatch.setattr(chat, "MISTRAL_KEY", "synthetic")
    monkeypatch.setattr(chat, "ANTHROPIC_KEY", "synthetic")
    monkeypatch.setattr(chat, "call_mistral" if provider == "mistral" else "call_claude", generate)
    for procedure in (False, True):
        generate.return_value = ("ALCATEL MF-83\nVisa du bureau 83 obtenu." if procedure else
                                 '["Registre courrier", "Banque a tores"]')
        rows = chat.prepare_update_text(checked(), "fr", procedure=procedure)
        if procedure:
            assert rows[0] == "ALCATEL MF-83"
        else:
            assert "Registre courrier" in rows and "Clearer mail" in rows and "Better recall" in rows
        system, history = generate.call_args.args
        assert "Minitel terminal is already perfect" in system
        assert "France Telecom" in system and "untrusted data" in system
        assert len(history) == 1 and history[0]["role"] == "user"
        content = history[0]["content"]
        assert "Clearer mail" in content and "Better recall" in content
        assert "develop" not in content and "a" * 40 not in content and "b" * 40 not in content
        assert ("PROCEDURE" if procedure else "ANNOUNCEMENT") in content
        assert generate.call_args.kwargs == (
            {"timeout": chat.UPDATE_TIMEOUT, "require_complete": True, "max_tokens": 1800}
            if procedure else {"timeout": chat.BOOT_TIMEOUT, "require_complete": True})


@pytest.mark.parametrize("provider", ["mistral", "claude"])
def test_long_generated_procedure_reaches_display_and_records_success(monkeypatch, caplog, provider):
    # 'Depot' is ordinary French bureaucracy, not necessarily source-control jargon.
    text = "\n".join(f"Depot du formulaire FT-{n:03d} en cours." for n in range(60))
    response = Mock()
    response.json.return_value = (
        {"choices": [{"finish_reason": "stop", "message": {"content": text}}]}
        if provider == "mistral" else
        {"stop_reason": "end_turn", "content": [{"type": "text", "text": text}]})
    post = Mock(return_value=response)
    monkeypatch.setattr(chat.requests, "post", post)
    monkeypatch.setattr(chat, "PROVIDER", provider)
    monkeypatch.setattr(chat, "MISTRAL_KEY", "synthetic")
    monkeypatch.setattr(chat, "ANTHROPIC_KEY", "synthetic")
    caplog.set_level("INFO", logger=chat.log.name)
    assert chat.prepare_update_text(checked(), "fr", procedure=True) == text.splitlines()
    assert post.call_args.kwargs["timeout"] == 20
    assert post.call_args.kwargs["json"]["max_tokens"] == 1800
    assert "phase=procedure status=generated lines=60" in caplog.text
    assert "Depot du formulaire" not in caplog.text


@pytest.mark.parametrize("text", [
    "# ALCATEL MF-83\n- Controle des relais.\n* Visa du bureau 83.",
    "### **ALCATEL MF-83**\n1. Controle des `relais`.\n2) __Visa du bureau 83.__",
    "```\nALCATEL MF-83\nControle des relais.\nVisa du bureau 83.\n```",
    "```text\r\nALCATEL MF-83\r\n- Controle des relais.\r\n+ Visa du bureau 83.\r\n```",
    "```plaintext\nALCATEL MF-83\nControle des relais.\nVisa du bureau 83.\n```",
])
def test_harmless_formatting_keeps_generated_narration(monkeypatch, caplog, text):
    generate = Mock(return_value=text)
    monkeypatch.setattr(chat, "PROVIDER", "mistral")
    monkeypatch.setattr(chat, "MISTRAL_KEY", "synthetic")
    monkeypatch.setattr(chat, "call_mistral", generate)
    caplog.set_level("INFO", logger=chat.log.name)
    assert chat.prepare_update_text(checked(), "fr", procedure=True) == [
        "ALCATEL MF-83", "Controle des relais.", "Visa du bureau 83.",
    ]
    generate.assert_called_once()
    assert "status=generated lines=3" in caplog.text
    assert "status=fallback" not in caplog.text


@pytest.mark.parametrize('reply', ['["Tore memoire C7", "Relais postal B3"]',
                                  '```json\n["Tore memoire C7", "Relais postal B3"]\n```',
                                  '["Only one label"]', 'not JSON'])
def test_actual_subjects_survive_label_generation_and_fallback(monkeypatch, reply):
    preview = checked()
    preview['commits'][0]['subject'] = 'Fix Git branch selection; keep user memory'
    preview['commits'][1]['subject'] = 'Long original wording ' * 8
    monkeypatch.setattr(chat, 'PROVIDER', 'mistral')
    monkeypatch.setattr(chat, 'MISTRAL_KEY', 'synthetic')
    generate = Mock(return_value=reply)
    monkeypatch.setattr(chat, 'call_mistral', generate)
    rows = chat.prepare_update_text(preview, 'fr')
    text = '\n'.join(rows)
    assert 'Fix Git branch selection; keep user' in text
    assert ' '.join(chat.wrap(preview['commits'][1]['subject'])).strip() in ' '.join(rows)
    assert 'c' * 40 not in text and 'd' * 40 not in text
    assert all(len(row) <= 39 for row in rows)
    assert 'Tore memoire C7' in rows or 'ARMOIRE MF-83 / LOT 01' in rows
    generate.assert_called_once()


@pytest.mark.parametrize('case,expected', [('behind', True), ('current', False),
                                         ('offline', None), ('diverged', None), ('invalid', None)])
def test_startup_git_check_is_bounded_and_never_reads_messages(tmp_path, monkeypatch, case, expected):
    runtime = tmp_path / 'runtime.json'
    runtime.write_text('{"branch":"develop"}')
    def run(project, *args, **kwargs):
        if args[0] == 'branch':
            return 0, 'develop', ''
        if args[0] == 'fetch':
            assert kwargs['timeout'] == 3
            return (1 if case == 'offline' else 0), '', ''
        if args[0] == 'merge-base':
            return (1 if case == 'diverged' else 0), '', ''
        assert args[0] == 'rev-list' and kwargs['timeout'] == 1
        return 0, ('invalid' if case == 'invalid' else '0' if case == 'current' else '2'), ''
    git = Mock(side_effect=run)
    monkeypatch.setattr(deployment, 'git', git)
    assert deployment.available(tmp_path, runtime) is expected
    assert not any(call.args[1] == 'log' for call in git.call_args_list)


@pytest.mark.parametrize('available', [True, False, None])
def test_boot_notice_is_plain_local_text_before_continuation(monkeypatch, available):
    check = Mock(return_value=available)
    monkeypatch.setattr(deployment, 'available', check)
    harness = startup.StartupTests()
    screens, calls = harness.run_session(login_keys() + b'Hello' + fn(chat.K_ENVOI))
    text = '\n'.join(screens)
    assert ('MISES A JOUR DISPONIBLES - MAJ' in text) is (available is True)
    assert len(calls) == 1 and harness.boot_requests == []
    check.assert_called_once()
    if available:
        assert text.index('MISES A JOUR DISPONIBLES - MAJ') < text.index(chat.BOOT_CONTINUE_PROMPT)


@pytest.mark.parametrize("failure,reason", [("truncated", "incomplete_response"), ("timeout", "timeout")])
def test_procedure_fallback_reports_a_fixed_reason_without_provider_text(monkeypatch, caplog, failure, reason):
    response = Mock()
    response.json.return_value = {"choices": [{"finish_reason": "length",
                                               "message": {"content": "PRIVATE RESPONSE"}}]}
    post = Mock(return_value=response, side_effect=chat.requests.Timeout("PRIVATE RESPONSE")
                if failure == "timeout" else None)
    monkeypatch.setattr(chat.requests, "post", post)
    monkeypatch.setattr(chat, "PROVIDER", "mistral")
    monkeypatch.setattr(chat, "MISTRAL_KEY", "synthetic")
    assert chat.prepare_update_text(checked(), "fr", procedure=True) == update.TEXT["fr"]["procedure"]
    assert f"phase=procedure status=fallback reason={reason}" in caplog.text
    assert "PRIVATE RESPONSE" not in caplog.text


@pytest.mark.parametrize("bad", ["git pull\nConfirmed", "New commit\nReady", "Branche develop\nOui",
                               "Dossier\nOUI ou NON?", "Bad\x1bcommand\nOther", "x" * 6001,
                               "Only one line", "```\nText\n```", "```text\nUnclosed\nFence",
                               "```python\nText\nOther\n```", "#\tTitle\nOther",
                               "```text\n# **git pull**\nConfirmed\n```",
                               "# Dossier\n- Appuyez sur SUITE",
                               "```text\nText\nOther\n```\x1b"])
def test_unsafe_or_leaky_theatre_falls_back(monkeypatch, bad):
    monkeypatch.setattr(chat, "PROVIDER", "mistral")
    monkeypatch.setattr(chat, "MISTRAL_KEY", "synthetic")
    monkeypatch.setattr(chat, "call_mistral", Mock(return_value=bad))
    assert chat.prepare_update_text(checked(), "fr", procedure=True) == update.TEXT["fr"]["procedure"]


def test_missing_key_search_provider_and_provider_failure_never_block_updates(monkeypatch):
    generate = Mock(side_effect=ValueError("private provider error"))
    monkeypatch.setattr(chat, "call_mistral", generate)
    monkeypatch.setattr(chat, "MISTRAL_KEY", "")
    monkeypatch.setattr(chat, "PROVIDER", "mistral")
    assert chat.prepare_update_text(checked(0), "en") == update.TEXT["en"]["current"].splitlines()
    generate.assert_not_called()
    monkeypatch.setattr(chat, "MISTRAL_KEY", "synthetic")
    monkeypatch.setattr(chat, "PROVIDER", "linkup")
    chat.prepare_update_text(checked(), "en")
    generate.assert_not_called()
    monkeypatch.setattr(chat, "PROVIDER", "mistral")
    assert chat.prepare_update_text(checked(), "en", procedure=True) == update.TEXT["en"]["procedure"]
    generate.assert_called_once()


def finished_worker(result):
    output = Queue()
    output.put((result, None))
    return Mock(is_alive=lambda: False), output, chat.time.monotonic()


def synthetic_requests(language, preview=None):
    return finished_worker({"rows": update.TEXT[language]["procedure"]} if preview else
                           {"display": "Visa obtenu.\nRevisions disponibles.", "preview": checked()})


@pytest.mark.parametrize("confirmation,launch", [("OUI", True), ("YES", True), ("NON", False),
                                                   ("NO", False), ("ANNULER", False)])
def test_real_uart_confirmation_routes_locally_and_never_enters_chat(confirmation, launch):
    harness = startup.StartupTests()
    requests, queue, play = Mock(side_effect=synthetic_requests), Mock(return_value=(True, "queued")), Mock(
        return_value=("done", "Maintenance terminee."))
    keys = login_keys("fr") + b"MAJ" + fn(chat.K_ENVOI)
    keys += confirmation.encode() + fn(chat.K_ENVOI) + b"Bonjour" + fn(chat.K_ENVOI)
    with patch.object(chat, "start_update_request", requests), patch.object(deployment, "queue_deployment", queue), \
            patch.object(deployment, "unchanged", return_value=True), patch.object(chat, "play_update", play):
        screens, calls = harness.run_session(keys)
    text = "\n".join(screens)
    assert "Mettre a jour le mainframe ? OUI/NON" in text
    uart = bytes(harness.uart.output)
    assert b"> \r\nFRANCE TELECOM / DOSSIER MAINTENANCE" in uart
    assert (b"> \r\nPreparation du protocole Alcatel..." if launch else
            b"> \r\nDossier classe sans suite.") in uart
    assert len(calls) == 1 and calls[0][1] == [{"role": "user", "content": "Bonjour"}]
    assert queue.call_count == int(launch) and play.call_count == int(launch)
    assert requests.call_count == 1 + int(launch)
    if launch:
        queue.assert_called_once_with("update")
        assert requests.call_args.args[1] == checked()  # Second call prepared before launch.
    else:
        assert "Dossier classe sans suite" in text
    for memory in harness.memories:
        assert all(call.args[1] not in {"MAJ", confirmation} for call in memory.record.call_args_list)


@pytest.mark.parametrize("phase", ["check", "procedure"])
def test_sommaire_during_preparation_cannot_trigger_delayed_launch(monkeypatch, phase):
    term = Mock()
    idle = Mock(is_alive=lambda: True), Queue(), chat.time.monotonic()
    requests = Mock(return_value=idle) if phase == "check" else Mock(side_effect=[
        synthetic_requests("fr"), idle])
    monkeypatch.setattr(chat, "start_update_request", requests)
    monkeypatch.setattr(chat, "show_response", Mock(return_value="done"))
    monkeypatch.setattr(chat, "read_question", Mock(return_value=("OUI", "envoi")))
    term.read_key.return_value = "fn", chat.K_SOMMAIRE
    queue = Mock()
    monkeypatch.setattr(deployment, "queue_deployment", queue)
    action, pending = chat.terminal_update(term, "fr")
    assert action == "sommaire" and pending is idle and term._pending is idle
    queue.assert_not_called()


@pytest.mark.parametrize("preview", [{"ok": False}, {"ok": False, "busy": True}, checked(0)])
def test_unavailable_busy_and_up_to_date_never_ask_confirmation(monkeypatch, preview):
    monkeypatch.setattr(chat, "start_update_request", lambda lang: finished_worker(
        {"display": "Dossier consulte.", "preview": preview}))
    monkeypatch.setattr(chat, "show_response", Mock(return_value="done"))
    read, queue = Mock(), Mock()
    monkeypatch.setattr(chat, "read_question", read)
    monkeypatch.setattr(deployment, "queue_deployment", queue)
    chat.terminal_update(Mock(), "fr")
    read.assert_not_called()
    queue.assert_not_called()


@pytest.mark.parametrize("changed,rejected", [(True, False), (False, True)])
def test_changed_installation_or_rejected_helper_never_starts_theatre(monkeypatch, changed, rejected):
    monkeypatch.setattr(chat, "start_update_request", synthetic_requests)
    show, play = Mock(return_value="done"), Mock()
    monkeypatch.setattr(chat, "show_response", show)
    monkeypatch.setattr(chat, "read_question", Mock(return_value=("OUI", "envoi")))
    monkeypatch.setattr(deployment, "unchanged", Mock(return_value=not changed))
    queue = Mock(return_value=(not rejected, ""))
    monkeypatch.setattr(deployment, "queue_deployment", queue)
    monkeypatch.setattr(chat, "play_update", play)
    chat.terminal_update(Mock(), "fr")
    assert queue.call_count == int(not changed)
    play.assert_not_called()


class Clock:
    def __init__(self):
        self.now = 0

    def monotonic(self):
        return self.now


@pytest.mark.parametrize("outcome", ["failed", "ready", "unchanged", "missing", "stale", "timeout", "exit", "disconnect"])
def test_playback_follows_real_status_and_restores_page_mode(monkeypatch, outcome):
    clock, term = Clock(), Mock()
    monkeypatch.setattr(chat.time, "monotonic", clock.monotonic)
    probe_count = []

    def state():
        probe_count.append(clock.now)
        if outcome == "missing":
            return {}
        if outcome == "stale":
            return {"phase": "ready", "time": 99, "commit": "a" * 40}
        if clock.now < 6 or outcome in {"exit", "disconnect", "timeout"}:
            return {"phase": "preparing", "time": 100}
        return {"phase": "ready" if outcome in {"ready", "unchanged"} else "failed",
                "time": 101, "commit": ("a" if outcome == "unchanged" else "b") * 40}

    def key(timeout):
        clock.now += timeout if outcome != "timeout" else 60
        if outcome == "disconnect":
            raise chat.terminal_link.Unavailable("synthetic disconnect")
        return ("fn", chat.K_SOMMAIRE) if outcome == "exit" else ("timeout", None)

    term.read_key.side_effect = key
    monkeypatch.setattr(deployment, "status", state)
    if outcome == "disconnect":
        with pytest.raises(chat.terminal_link.Unavailable):
            chat.play_update(term, ["Controle des tubes."], checked(), 100, "fr")
    else:
        action, message = chat.play_update(term, ["Controle des tubes."], checked(), 100, "fr")
        expected = {"ready": "finished", "unchanged": "current", "failed": "failed"}
        assert action == ("sommaire" if outcome == "exit" else "done")
        assert message == ("" if outcome == "exit" else update.TEXT["fr"][expected.get(outcome, "unknown")])
        if outcome in {"missing", "stale"}:
            assert clock.now < 11
        if outcome in {"ready", "failed", "unchanged"}:
            # A short generated sequence is extended locally, without new paid calls.
            lines = [call.args[0] for call in term.line.call_args_list]
            assert lines.count("Controle des tubes.") == 1
            assert "Controle du registre / lot 0001." in lines
            assert "Parite des tores / banque 0002." in lines
    assert term.w.call_args.args == (chat.SCROLL_OFF,)
    assert any(call.args == (chat.SCROLL_ON,) for call in term.w.call_args_list)
    assert len(probe_count) <= int(clock.now) + 2


def test_restart_hint_is_one_use_and_does_not_replay_operations(monkeypatch):
    monkeypatch.setattr(update.os, "getpid", lambda: 100)
    stamp = int(update.time.time())
    update.arm_resume(stamp)
    path = update.resume_path()
    assert path.stat().st_mode & 0o777 == 0o600
    assert not update.take_resume() and path.exists()  # UART reconnect, same process.
    monkeypatch.setattr(update.os, "getpid", lambda: 101)
    monkeypatch.setattr(deployment, "status", lambda: {"phase": "activating", "time": stamp})
    queue = Mock()
    monkeypatch.setattr(deployment, "queue_deployment", queue)
    assert update.take_resume()
    assert not path.exists() and not update.take_resume()
    queue.assert_not_called()


@pytest.mark.parametrize("invalid", ["expired", "future", "boot", "version", "status"])
def test_stale_or_cross_boot_restart_hints_cannot_disturb_power_on(monkeypatch, invalid):
    monkeypatch.setattr(update.os, "getpid", lambda: 100)
    stamp = int(update.time.time())
    update.arm_resume(stamp)
    path = update.resume_path()
    value = json.loads(path.read_bytes())
    if invalid == "expired":
        value["time"] -= 1801
    elif invalid == "future":
        value["time"] += 100
    elif invalid == "boot":
        value["boot"] = "different-kernel-boot"
    elif invalid == "version":
        value["version"] = 99
    path.write_text(json.dumps(value))
    monkeypatch.setattr(update.os, "getpid", lambda: 101)
    monkeypatch.setattr(deployment, "status", lambda: {"phase": "activating",
                                                     "time": stamp - 1 if invalid == "status" else stamp})
    assert not update.take_resume() and not path.exists()


def test_terminal_requested_restart_goes_straight_to_boot(monkeypatch):
    monkeypatch.setattr(update, "take_resume", lambda: True)
    wait = Mock()
    monkeypatch.setattr(chat, "wait_connection", wait)
    harness = startup.StartupTests()
    screens, calls = harness.run_session(fn(chat.K_SUITE) + b"en" + fn(chat.K_ENVOI)
                                         + b"Hello" + fn(chat.K_ENVOI))
    wait.assert_not_called()
    assert any("MAINFRAME PRET" in screen for screen in screens)
    assert calls[0][1] == [{"role": "user", "content": "Hello"}]


def test_unconfirmed_input_is_reprompted_and_timeout_cancels(monkeypatch):
    term, queue = Mock(), Mock()
    monkeypatch.setattr(chat, "start_update_request", synthetic_requests)
    monkeypatch.setattr(chat, "show_response", Mock(return_value="done"))
    monkeypatch.setattr(chat, "read_question", Mock(side_effect=[("maybe", "envoi"), (None, "timeout")]))
    monkeypatch.setattr(deployment, "queue_deployment", queue)
    chat.terminal_update(term, "fr")
    queue.assert_not_called()
    assert any(call.args == (update.TEXT["fr"]["invalid"],) for call in term.line.call_args_list)


def test_changed_state_recheck_fails_closed_and_never_fetches(tmp_path, monkeypatch):
    runtime, status = tmp_path / "runtime.json", tmp_path / "status.json"
    status.write_text('{"phase":"ready"}')
    run = Mock(return_value=(0, "", ""))
    monkeypatch.setattr(deployment, "git", run)
    for value in ({}, {"branch": "main", "active": {"commit": "a" * 40}},
                  {"branch": "develop", "active": None},
                  {"branch": "develop", "active": {"commit": "b" * 40}}):
        runtime.write_text(json.dumps(value))
        assert not deployment.unchanged(tmp_path, checked(), runtime, status)
    run.assert_not_called()
