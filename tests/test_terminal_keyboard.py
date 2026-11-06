"""Verified firmware case mode; input parsers and stored secrets remain unchanged."""


import pytest
from test_console_terminal import Window
from test_rose_echo import submitted
from test_terminal_display import VisibleUART
from test_terminal_serial_timing import Clock, QueuedUART
from test_terminal_startup import fn

from services import minitel_chatgpt as chat
from services import rose_terminal, terminal_link
from services.console_terminal import ConsoleSerial


class NativeUART(VisibleUART, chat.serial.SerialBase):
    # Explicit physical-transport identity, with no OS serial port opened.
    baudrate, write_timeout, out_waiting = 1200, 2, 0
    port = "synthetic-uart"

    def __init__(self, clock, keys=b"", *, original_lower=False, supported=True, ignore_lower=False):
        VisibleUART.__init__(self, keys)
        self.clock = clock
        self.lower = original_lower
        self.supported, self.ignore_lower = supported, ignore_lower
        self.early = b""
        self.did_close = False
        self.malformed_lower = False
        self.drop_lower_ack = False

    def write(self, data):
        result = super().write(data)
        if data in (chat.KEYBOARD_STATUS, chat.KEYBOARD_LOWER, chat.KEYBOARD_UPPER):
            if data != chat.KEYBOARD_STATUS and (data != chat.KEYBOARD_LOWER or not self.ignore_lower):
                self.lower = data == chat.KEYBOARD_LOWER
            if self.supported and not (data == chat.KEYBOARD_LOWER and self.drop_lower_ack):
                status = 0x40 | (8 if self.lower else 0)
                if data == chat.KEYBOARD_LOWER and self.malformed_lower:
                    status = 0x20
                self.incoming.extend(self.early + b"\x1b\x3a\x73" + bytes([status]))
                self.early = b""
        return result

    def read(self, count):
        self.clock.sleep(.01)
        # Synthetic keys represent uppercase-default physical keystrokes. Mode
        # mapping happens inside this fake hardware, never in application code.
        if self.lower and not self.incoming and self.keys and not self.function:
            code = self.keys[0]
            if isinstance(code, int) and (65 <= code <= 90 or 97 <= code <= 122):
                self.keys[0] = ord(chr(code).swapcase())
        return super().read(count)

    def close(self):
        self.did_close = True


@pytest.fixture
def native(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(chat.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(chat.time, "sleep", clock.sleep)
    def make(keys=b"", mode="lowercase", **kwargs):
        monkeypatch.setenv("MINITEL_INPUT_CASE", mode)
        uart = NativeUART(clock, keys, **kwargs)
        return chat.Term(uart), uart, clock
    return make


def test_native_applies_after_connection_and_restores_original_on_close(native, monkeypatch):
    term, uart, _ = native(fn(chat.K_SOMMAIRE) + b"AZaz09!?" + fn(chat.K_REPET))
    assert term.physical and uart.output == b""
    chat.wait_connection(term)
    assert term.input_case == "lowercase" and uart.lower
    assert uart.output == chat.KEYBOARD_STATUS + chat.KEYBOARD_LOWER
    monkeypatch.setenv("MINITEL_INPUT_CASE", "legacy")  # No live mid-entry setting changes.
    assert [term.read_key(1) for _ in range(8)] == [("char", ord(c)) for c in "azAZ09!?"]
    assert term.read_key(1) == ("fn", chat.K_REPET)
    term.close()
    assert not uart.lower and uart.did_close and not term._input_bytes
    assert uart.output.endswith(chat.KEYBOARD_UPPER)


def test_legacy_applies_and_restores_initial_lowercase(native):
    term, uart, _ = native(b"AZaz", mode="legacy", original_lower=True)
    term.start_presence()
    assert term.input_case == "legacy" and not uart.lower
    assert [term.read_key(1)[1] for _ in range(4)] == list(b"AZaz")
    term.close()
    assert uart.lower and uart.output.endswith(chat.KEYBOARD_LOWER)


@pytest.mark.parametrize("mode", ["lowercase", "legacy"])
def test_native_mode_covers_hidden_passwords_and_normal_editor_without_changing_values(native, monkeypatch, mode):
    password = "PaSsW0rd!?"
    typed = password.swapcase() if mode == "lowercase" else password
    monkeypatch.setenv("ROSE_PASSWORD", password)
    term, uart, _ = native(submitted(typed), mode=mode)
    term.start_presence()
    before = dict(uart.routes)
    assert rose_terminal.gate(term) == (True, "submit")
    assert uart.routes == before
    assert password.encode() not in uart.output
    assert all(copies == 0 for _, copies in uart.echo)
    uart.keys.extend(submitted(typed))
    chat.show_response(term, "Answer")
    assert chat.read_question(term) == (password, "envoi")
    assert uart.lines()[21].startswith("> " + password)


@pytest.mark.parametrize("failure", ["ignore_lower", "drop_lower_ack", "malformed_lower"])
def test_lowercase_failure_confirms_legacy_without_python_swap(native, caplog, failure):
    term, uart, _ = native()
    setattr(uart, failure, True)
    term.start_presence()
    assert term.input_case == "legacy" and not uart.lower
    uart.keys.extend(b"AZ")
    assert [term.read_key(1)[1] for _ in range(2)] == list(b"AZ")
    assert "fallback=legacy" in caplog.text


def test_unsupported_mode_is_bounded_preserves_input_and_reports_uncertainty(native, caplog):
    term, uart, clock = native(b"AZ", supported=False)
    term.start_presence()
    assert 2 <= clock.now < 3
    assert term.input_case == "unconfirmed"
    assert [term.read_key(1)[1] for _ in range(2)] == list(b"AZ")
    assert "fallback=unconfirmed" in caplog.text
    assert "AZ" not in caplog.text
    term.close()
    assert uart.did_close


def test_early_keys_preserved_and_protocol_status_not_exposed(native):
    term, uart, _ = native()
    uart.early = fn(chat.K_SOMMAIRE) + b"early"
    term.start_presence()
    assert term.read_key(1) == ("fn", chat.K_SOMMAIRE)
    assert [term.read_key(1)[1] for _ in range(5)] == list(b"early")
    assert not term._input_bytes


def test_restoration_failure_still_closes_and_clears_history(native, caplog):
    term, uart, _ = native()
    term.start_presence()
    term.prompt_history.add("private synthetic test")
    uart.supported = False
    term.close()
    assert uart.did_close and term.prompt_history.items == []
    assert "restoration=unconfirmed" in caplog.text
    assert "private synthetic test" not in caplog.text


def test_typeahead_has_a_bound(native):
    term, uart, _ = native()
    term._input_bytes.extend(b"X" * chat.io_limits.MAX_QUESTION_CHARS)
    uart.early = b"X" * 100
    with pytest.raises(terminal_link.Unavailable, match="keyboard_typeahead_overflow"):
        term.configure_keyboard()


@pytest.mark.parametrize("mode", ["lowercase", "legacy"])
def test_console_bypasses_native_configuration_and_retains_full_ascii_case(monkeypatch, mode):
    monkeypatch.setenv("MINITEL_INPUT_CASE", mode)
    uart = ConsoleSerial(Window(["A", "z"]))
    term = chat.Term(uart)
    term.configure_keyboard()
    assert not term.physical and term.input_case == "console"
    assert [term.read_key(1)[1] for _ in range(2)] == list(b"Az")


def test_native_mode_reply_budget_includes_display_backlog(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(chat.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(chat.time, "sleep", clock.sleep)
    class PacedModeUART(QueuedUART):
        def deliver(self, data):
            super().deliver(data)
            if data == chat.KEYBOARD_STATUS:
                for index, code in enumerate(b"\x1b\x3a\x73\x40"):
                    self.later(self.clock.now + .02 + index / 120, lambda code=code: self.incoming.append(code))
    uart = PacedModeUART(clock)
    term = chat.Term(uart)
    term.w(b"X" * 1200)
    assert term.keyboard_mode_status(chat.KEYBOARD_STATUS) == 0x40
    assert 10 < clock.now < 11


def test_sigterm_uses_clean_shutdown_and_restores_handler(monkeypatch):
    original = chat.signal.getsignal(chat.signal.SIGTERM)
    def serve(*args):
        chat.signal.getsignal(chat.signal.SIGTERM)(chat.signal.SIGTERM, None)
    monkeypatch.setattr(chat.terminal_link, "serve", serve)
    chat.main()
    assert chat.signal.getsignal(chat.signal.SIGTERM) == original


def test_admin_keyboard_setting_is_authenticated_csrf_protected_and_validated(console):
    from services import admin_ui as admin
    client, headers, _ = console
    anonymous = admin.app.test_client()
    assert anonymous.post("/save-terminal", data={"input_case": "lowercase"}).status_code == 302
    assert client.post("/save-terminal", data={"input_case": "lowercase"}).status_code == 403
    for value in ("", "python", "LOWERCASE", "legacy\nOTHER=1"):
        assert client.post("/save-terminal", data={"input_case": value}, headers=headers).status_code == 400
    assert "MINITEL_INPUT_CASE" not in admin.read_env()
    admin.restart_terminal.assert_not_called()


@pytest.mark.parametrize("mode,restarted", [("lowercase", True), ("legacy", True), ("legacy", False)])
def test_admin_keyboard_setting_persists_and_reports_restart_result(console, mode, restarted):
    from services import admin_ui as admin
    client, headers, _ = console
    admin.write_env_key("OTHER", "preserve")
    admin.restart_terminal.return_value = restarted
    response = client.post("/save-terminal", data={"input_case": mode}, headers=headers)
    assert response.status_code == 302 and "section=maintenance" in response.location
    assert admin.read_env() == {"OTHER": "preserve", "MINITEL_INPUT_CASE": mode}
    admin.restart_terminal.assert_called_once_with()
    with client.session_transaction() as session:
        assert session["flash_ok"] is restarted
        assert "enregistrée" in session["flash"]
        assert ("confirmé" if restarted else "non confirmé") in session["flash"]
    page = client.get("/?section=maintenance").text
    assert 'action=/save-terminal' in page
    assert f'value={mode} selected' in page


def test_admin_keyboard_save_failure_does_not_restart(console, monkeypatch):
    from services import admin_ui as admin
    client, headers, _ = console
    def fail(*args):
        raise admin.config_store.StorageFailure("synthetic failure")
    monkeypatch.setattr(admin, "write_env_key", fail)
    assert client.post("/save-terminal", data={"input_case": "lowercase"}, headers=headers).status_code == 503
    admin.restart_terminal.assert_not_called()


def test_invalid_runtime_setting_uses_confirmed_legacy(native, caplog):
    term, uart, _ = native(mode="invalid")
    term.start_presence()
    assert term.input_case == "legacy" and not uart.lower
    assert "invalid_configuration" in caplog.text


def test_admin_displays_safe_original_mode_for_invalid_manual_configuration(console):
    from services import admin_ui as admin
    client, _, _ = console
    admin.write_env_key("MINITEL_INPUT_CASE", "invalid")
    page = client.get("/?section=maintenance").text
    assert "value=legacy selected" in page


def test_new_physical_connection_reapplies_configuration(native):
    old, uart, _ = native()
    old.start_presence()
    old.prompt_history.add("old session")
    old.close()
    new, other, _ = native(mode="lowercase")
    new.start_presence()
    assert new.input_case == "lowercase" and other.lower
    assert new.prompt_history.items == [] and old.prompt_history.items == []
    assert uart.did_close
