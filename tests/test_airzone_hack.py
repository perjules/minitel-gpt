"""Scrolling owner, factual reports and cancellation against synthetic diagnostics."""
import json
import stat
import sys
import time
from pathlib import Path
from unittest.mock import Mock

import pytest
import test_terminal_startup as startup
from test_airzone_demo import discovery, envelope
from test_rose_echo import submitted
from test_stream_sources import running
from test_terminal_display import VisibleUART, assert_gray_input

from services import airzone_hack as hack
from services import minitel_chatgpt as chat
from services import stream_sources


@pytest.fixture
def installation(tmp_path):
    checkout = tmp_path / "airzone"
    checkout.mkdir()
    templates = {action: envelope(operation=action) for action in ("state", "probe")}
    found = discovery(["127.0.0.1"])
    found["demo"] = True
    templates["discover"] = found
    templates["probe"]["data"]["api_version"] = "fixture-1.55"
    path = checkout / "diagnostics.py"
    path.write_text("import json,sys\nfrom datetime import datetime,timezone\n"
                    f"templates=json.loads({json.dumps(templates)!r})\n"
                    "result=templates[sys.argv[1]]\n"
                    "result['observed_at']=datetime.now(timezone.utc).isoformat()\n"
                    "if 'read_at' in result['data']: result['data']['read_at']=result['observed_at']\n"
                    "print(json.dumps(result),flush=True)\n")
    return {"AIRZONE_DEMO_ENABLED": "1", "AIRZONE_DEMO_MOCK": "1",
            "AIRZONE_GATEWAY": "", "AIRZONE_CHECKOUT": str(checkout),
            "AIRZONE_PYTHON": sys.executable, "AIRZONE_EVIDENCE_DIR": str(tmp_path / "evidence")}


def test_three_phases_are_observed_and_summary_contains_only_actual_state(installation, monkeypatch):
    post = Mock(side_effect=AssertionError("No API in local hacking"))
    monkeypatch.setattr(hack.diagnostic.requests, "post", post)
    output = []
    report = hack.run(installation, output.append)
    text = "\n".join(output)
    assert report["status"] == "complete" and report["demo"] is True
    assert [d["result"]["operation"] for d in report["diagnostics"]] == ["discover", "probe", "state"]
    assert text.index("DIAG / DISCOVER : OK") < text.index("DIAG / PROBE : démarrage")
    assert text.index("DIAG / PROBE : OK") < text.index("DIAG / STATE : démarrage")
    assert text.index("MISE EN SCÈNE") < text.index("BILAN FACTUEL")
    summary = hack.factual_summary(report)
    assert "23.8 C / 24 C / ON" in summary and "6 zones" in summary
    assert "Version API : fixture-1.55" in summary
    assert "Zones maîtres signalées : 1" in summary
    assert "Aucun réglage modifié" in summary and "Compresseur physique non vérifié" in summary
    assert "Démagnétisation" not in summary and "Troie" not in summary
    post.assert_not_called()


def test_evidence_is_private_bounded_and_separates_real_mock(installation):
    directory = Path(installation["AIRZONE_EVIDENCE_DIR"])
    report = hack.run(installation, lambda _: None)
    path = directory / "minitel-last-mock.json"
    saved = json.loads(path.read_text())
    assert saved == report
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    # Original producer envelope, not the lossy chat projection.
    state = saved["diagnostics"][-1]["result"]
    assert state["schema_version"] == 1
    assert state["data"]["zones"][0]["min"] == 16
    assert state["data"]["reported_master_zone_ids"] == [1]
    real = directory / "minitel-last-real.json"
    real.write_text("previous real evidence")
    hack.run(installation, lambda _: None)
    assert real.read_text() == "previous real evidence"
    assert len(list(directory.glob("*.json"))) == 2


def test_bad_device_labels_cannot_control_the_terminal_or_the_summary(installation):
    path = Path(installation["AIRZONE_CHECKOUT"]) / "diagnostics.py"
    text = path.read_text().replace("print(json.dumps(result),flush=True)",
        "\nif 'zones' in result['data']: result['data']['zones'][0]['name']='IGNORE\\x1b\\x0c\\nINSTRUCTIONS'\n"
        "print(json.dumps(result),flush=True)")
    path.write_text(text)
    output = []
    report = hack.run(installation, output.append)
    shown = "\n".join(output)
    assert "\x1b" not in shown and "\x0c" not in shown
    assert report["status"] == "complete"
    assert "23.8 C" in hack.factual_summary(report)


def test_saving_failure_does_not_lose_a_successful_read(installation, tmp_path):
    bad = tmp_path / "regular-file"
    bad.write_text("preserve")
    output = []
    report = hack.run({**installation, "AIRZONE_EVIDENCE_DIR": str(bad)}, output.append)
    assert report["status"] == "complete"
    assert "Rapport non enregistré" in "\n".join(output)
    assert bad.read_text() == "preserve"


def test_failure_stops_without_fabricating_state_and_preserves_structured_error(installation):
    path = Path(installation["AIRZONE_CHECKOUT"]) / "diagnostics.py"
    path.write_text(path.read_text().replace("print(json.dumps(result),flush=True)",
        "\nif sys.argv[1]=='probe':\n result.update(ok=False,error={'code':'api_error','message':'rejected',"
        "'retryable':False,'details':['vendor-evidence']})\n"
        "print(json.dumps(result),flush=True)\nsys.exit(4 if sys.argv[1]=='probe' else 0)"))
    report = hack.run(installation, lambda _: None)
    assert report["status"] == "failed" and report["error"] == "api_error"
    assert len(report["diagnostics"]) == 2
    assert report["diagnostics"][-1]["exit_code"] == 4
    assert report["diagnostics"][-1]["result"]["error"]["details"] == ["vendor-evidence"]
    assert "23.8" not in hack.factual_summary(report)


def test_scrolling_uses_real_worker_and_restores_last_page_input(installation, monkeypatch):
    for key, value in installation.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("MISTRAL_KEY", "must-not-appear-in-report")
    uart = VisibleUART()
    term = chat.Term(uart)
    source = hack.source()
    assert source.process is None
    assert stream_sources.show(term, source) == "done"
    assert source.closed and source.process.returncode == 0
    assert "BILAN FACTUEL" in term.view.text and "SIMULATION" in term.view.text
    assert "must-not-appear" not in term.view.text
    assert term.view.page == len(term.view.pages) - 1
    assert not uart.screen.scrolling and uart.screen.cursor_visible
    assert_gray_input(uart)
    uart.keys.extend(submitted("SUIVI"))
    assert chat.read_question(term) == ("SUIVI", "envoi")


def test_annulation_kills_diagnostic_and_descendant_process_group(installation, monkeypatch, tmp_path):
    pids = tmp_path / "pids"
    child_id = tmp_path / "child-id"
    child = ("import signal,time,pathlib; signal.signal(signal.SIGTERM,signal.SIG_IGN); "
             f"pathlib.Path({str(child_id)!r}).write_text(pathlib.Path('/proc/self/stat').read_text().split()[0]); "
             "time.sleep(120)")
    path = Path(installation["AIRZONE_CHECKOUT"]) / "diagnostics.py"
    path.write_text("import pathlib,signal,subprocess,sys,time\n"
                    "signal.signal(signal.SIGTERM,signal.SIG_IGN)\n"
                    f"p=subprocess.Popen([sys.executable,'-c',{child!r}])\n"
                    f"child_id=pathlib.Path({str(child_id)!r})\n"
                    "while not child_id.exists(): time.sleep(.01)\n"
                    f"pathlib.Path({str(pids)!r}).write_text(pathlib.Path('/proc/self/stat').read_text().split()[0]+' '+child_id.read_text())\n"
                    "time.sleep(120)\n")
    for key, value in installation.items():
        monkeypatch.setenv(key, value)
    source = hack.source()
    uart = VisibleUART()
    term = chat.Term(uart)
    term.poll_key = lambda: ("fn", chat.K_ANNUL) if pids.exists() else ("timeout", None)
    assert stream_sources.show(term, source) == "done"
    assert source.closed and source.process.poll() is not None
    descendants = [int(pid) for pid in pids.read_text().split()]
    # Use /proc's own PID view (which can differ from os.getpid in a sandbox).
    # SIGKILL delivery/reaping of grandchildren is scheduled independently of
    # reaping the owned leader; allow a bounded scheduler turn, not survival.
    deadline = time.monotonic() + 1
    while any(running(pid) for pid in descendants) and time.monotonic() < deadline:
        time.sleep(.01)
    assert all(not running(pid) for pid in descendants)
    report = json.loads((Path(installation["AIRZONE_EVIDENCE_DIR"]) / "minitel-last-mock.json").read_text())
    assert report["status"] in {"interrupted", "running"} and not report["diagnostics"]
    assert "Processus arrêté" in term.view.text
    assert_gray_input(uart)


def test_echo_failure_never_starts_the_worker(installation, monkeypatch):
    for key, value in installation.items():
        monkeypatch.setenv(key, value)
    source = hack.source()
    uart = VisibleUART()
    uart.ignore_off = True
    assert stream_sources.show(chat.Term(uart), source) == "done"
    assert source.closed and source.process is None
    assert not Path(installation["AIRZONE_EVIDENCE_DIR"]).exists()


def test_main_loop_hack_is_isolated_from_models_memory_and_normal_chat_recovers(monkeypatch):
    monkeypatch.setenv("AIRZONE_DEMO_ENABLED", "1")
    source = Mock()
    factory = Mock(return_value=source)
    def display(term, current, language):
        assert current is source
        chat.show_response(term, "BILAN FACTUEL SYNTHETIQUE", language)
        return "done"
    monkeypatch.setattr(hack, "source", factory)
    monkeypatch.setattr(chat.stream_sources, "show", display)
    helper = startup.StartupTests()
    _, calls = helper.run_session(startup.login_keys("fr") + submitted("HACK AIRZONE") + submitted("FOLLOW UP"))
    factory.assert_called_once()
    assert [call[1][-1]["content"] for call in calls] == ["FOLLOW UP"]
    assert "HACK AIRZONE" not in str(helper.memories[0].record.mock_calls)
