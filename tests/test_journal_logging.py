"""Bounded operational logging without opening or deleting legacy log files."""
import configparser
import errno
import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scripts import journal_reader, runtime
from services import admin_ui as admin
from services import minitel_chatgpt as chat
from services import storage_health

ROOT = Path(__file__).resolve().parents[1]


def test_journal_reader_has_fixed_units_no_pager_and_a_timeout(monkeypatch):
    run = Mock(return_value=SimpleNamespace(stdout="entry\n"))
    monkeypatch.setattr(journal_reader.subprocess, "run", run)
    assert journal_reader.read("wifi") == "entry\n"
    args = run.call_args.args[0]
    assert "--namespace=minitel-gpt" in args and "--unit=wifi-manager" in args
    assert "--no-pager" in args and "--lines=200" in args
    assert run.call_args.kwargs["timeout"] == 5
    run.reset_mock()
    with pytest.raises(KeyError):
        journal_reader.read("../../etc/shadow")
    run.assert_not_called()


def test_admin_log_view_is_allowlisted_and_handles_missing_journal(monkeypatch):
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout="one\ntwo\nthree\n"))
    monkeypatch.setattr(admin.subprocess, "run", run)
    assert admin.log_tail("chatgpt", 2) == "two\nthree"
    assert run.call_args.args[0] == ["sudo", "-n", "/usr/local/libexec/minitel-journal", "chatgpt"]
    run.reset_mock()
    assert "inconnu" in admin.log_tail("../../etc/shadow")
    run.assert_not_called()
    run.side_effect = subprocess.TimeoutExpired("journal", 7)
    assert "indisponible" in admin.log_tail("chatgpt")


def test_log_stream_failure_cannot_abort_terminal_input():
    # Logging failures (e.g. broken capture stream) must not become serial errors.
    class FullStream:
        def write(self, data):
            raise OSError(errno.ENOSPC, "full")

        def flush(self):
            pass

    import logging
    logger = logging.getLogger("test-full-stream")
    handler = logging.StreamHandler(FullStream())
    logger.addHandler(handler)
    try:
        logger.warning("one diagnostic")
    finally:
        logger.removeHandler(handler)
    term = chat.Term(transport=SimpleNamespace(read=Mock(return_value=b"A")))
    assert term.read_byte() == ord("A")


def test_services_share_one_bounded_journal_and_no_file_redirection():
    config = configparser.ConfigParser(interpolation=None)
    config.read(ROOT / "config/journald-minitel-gpt.conf")
    journal = config["Journal"]
    assert journal["SystemMaxUse"] == "64M"
    assert journal["RuntimeMaxUse"] == "16M"
    assert journal["MaxRetentionSec"] == "7day"
    for name in (*runtime.SERVICES, "minitel-housekeeping"):
        unit = configparser.ConfigParser(interpolation=None)
        unit.read(ROOT / f"config/{name}.service")
        assert unit["Service"]["LogNamespace"] == "minitel-gpt"
        assert unit["Service"]["StandardOutput"] == "journal"
        assert "append:" not in (ROOT / f"config/{name}.service").read_text()


def test_storage_status_reports_archives_without_following_symlinks(tmp_path, monkeypatch):
    project = tmp_path / "checkout"
    (project / "logs/rose").mkdir(parents=True)
    (project / "logs/chatgpt.log").write_bytes(b"x" * 100)
    (project / "logs/rose/test.jsonl").write_bytes(b"x" * 20)
    memory = tmp_path / "memory"
    memory.mkdir()
    (memory / "session.jsonl").write_bytes(b"x" * 40)
    external = tmp_path / "external"
    external.mkdir()
    (external / "private").write_bytes(b"x" * 1000)
    (memory / "link").symlink_to(external, target_is_directory=True)
    monkeypatch.setattr(storage_health.shutil, "disk_usage",
                        lambda _: SimpleNamespace(free=500, total=10000))
    result = storage_health.status(project, memory)
    assert result["warning"] and result["free_bytes"] == 500
    assert result["legacy_logs_and_rose"]["bytes"] == 120
    assert result["rose_transcripts"]["bytes"] == 20
    assert result["memory_and_conversations"]["bytes"] == 40
    assert (project / "logs/chatgpt.log").stat().st_size == 100
    assert storage_health.tree_usage(memory, limit=0)["partial"]


def test_storage_endpoint_requires_login_and_handles_unavailable_storage(monkeypatch):
    client = admin.app.test_client()
    assert client.get("/storage-status").status_code == 302
    with client.session_transaction() as session:
        session["logged_in"] = True
    monkeypatch.setattr(admin.storage_health, "status", Mock(side_effect=OSError("full")))
    assert client.get("/storage-status").status_code == 503


@pytest.mark.skipif(shutil.which("systemd-analyze") is None, reason="systemd syntax tool unavailable")
def test_systemd_accepts_units_and_timer_without_installing_them(tmp_path):
    paths = []
    for name in (*runtime.SERVICES, "minitel-housekeeping"):
        path = tmp_path / f"{name}.service"
        # Validate the real unit directives on the host; substitute only the Pi's
        # executable and working directory. Never start or install a service.
        text = (ROOT / f"config/{name}.service").read_text()
        text = re.sub(r"(?m)^ExecStart=.*$", "ExecStart=/usr/bin/true", text)
        text = re.sub(r"(?m)^WorkingDirectory=.*$", "WorkingDirectory=/tmp", text)
        path.write_text(text)
        paths.append(str(path))
    timer = tmp_path / "minitel-housekeeping.timer"
    timer.write_bytes((ROOT / "config/minitel-housekeeping.timer").read_bytes())
    subprocess.run(["systemd-analyze", "verify", "--man=no", *paths, str(timer)],
                   capture_output=True, text=True, check=True, timeout=20)


@pytest.mark.skipif(shutil.which("visudo") is None, reason="sudoers syntax tool unavailable")
def test_restricted_journal_sudo_rule_parses():
    subprocess.run(["visudo", "-c", "-f", str(ROOT / "config/minitel-gpt-journal-sudoers")],
                   capture_output=True, text=True, check=True, timeout=5)
