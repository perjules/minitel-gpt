"""Exercise startup through the real UART key decoder and conversation loop."""
import unittest
from collections import deque
from pathlib import Path
from queue import Queue
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import pytest

from services import minitel_chatgpt as chat


class EndOfInput(Exception):
    pass


class ReadTimeout(Exception):
    """A simulated idle read, distinct from an OS transport failure."""


class FakeSerial:
    def __init__(self, keys):
        self.keys = deque(keys)
        self.output = bytearray()
        self.consumed = bytearray()

    def read(self, count):
        if not self.keys:
            raise EndOfInput
        key = self.keys.popleft()
        # None represents a read_key timeout, patched at the input boundary.
        if key is None:
            raise ReadTimeout
        self.consumed.append(key)
        return bytes([key])

    def write(self, data):
        self.output.extend(data)


def fn(code):
    return bytes([chat.SEP, code])


def screen_text(data):
    """Strip colour/cursor controls, retaining line breaks and visible text."""
    visible = bytearray()
    skip = 0
    escape = False
    for code in data:
        if skip:
            skip -= 1
        elif escape:
            skip = code - 0x38 if code in (0x39, 0x3A, 0x3B) else 0
            escape = False
        elif code == chat.ESC:
            escape = True
        elif code in (chat.CR, chat.LF) or 0x20 <= code <= 0x7E:
            visible.append(code)
    return visible.decode("ascii")


def render_videotex(data):
    """Small screen model: page/rouleau mode, FF/RS, CR/LF and column wrap."""
    screen = [""] * 24
    row = column = 0
    scrolling = False
    index = 0

    def down():
        nonlocal row
        if row < 23:
            row += 1
        elif scrolling:
            screen.pop(0)
            screen.append("")
        else:
            row = 0

    while index < len(data):
        code = data[index]
        index += 1
        if code == 0x1B:
            command = data[index]
            index += 1
            if command == 0x3A:
                operation, mode = data[index:index + 2]
                index += 2
                if mode == 0x43:
                    scrolling = operation == 0x69
        elif code == 0x0C:
            screen = [""] * 24
            row = column = 0
        elif code == 0x1E:
            row = column = 0
        elif code == 0x0D:
            column = 0
        elif code == 0x0A:
            down()
        elif 0x20 <= code <= 0x7E:
            current = screen[row].ljust(column + 1)
            screen[row] = current[:column] + chr(code) + current[column + 1:]
            column += 1
            if column == 40:
                column = 0
                down()
    return screen, scrolling


def login_keys(language="en"):
    """Physical connection, boot continuation, then language (user UI is mocked)."""
    return fn(chat.K_SOMMAIRE) + fn(chat.K_SUITE) + language.encode() + fn(chat.K_ENVOI)


class StartupTests(unittest.TestCase):
    def run_session(self, keys, boot_text=None, boot_error=None, preset=None,
                    factual_reply=None):
        uart = FakeSerial(keys)
        calls, memories = [], []

        def memory(username="Test"):
            from services.user_registry import Registry
            registry=Registry()
            user=registry.resolve(username)
            instance = Mock(user=user, registry=registry, username=username, key="users/test", pending=None,
                            take_warning=lambda: "", context=lambda q: "",
                            interested=lambda q: False)
            instance.saved_language.return_value = None
            memories.append(instance)
            return instance

        selected_user = Mock(side_effect=lambda *a, **kw: (memory(), "envoi"))

        def reply(system, history):
            calls.append((system, [dict(turn) for turn in history], bytes(uart.consumed)))
            return "17 x 23 = 391."

        def start(system, history, language, options, knowledge, **kwargs):
            output = Queue()
            try:
                result = chat.call_answer(system, history, language, options, knowledge)
                output.put((result, None))
            except (ValueError, TypeError, RuntimeError, chat.requests.RequestException) as error:
                output.put((None, error))
            return Mock(is_alive=lambda: False), output, chat.time.monotonic()

        real_read_key = chat.Term.read_key

        def read_key(term, timeout):
            try:
                return real_read_key(term, timeout)
            except ReadTimeout:
                return 'timeout', None

        response = Mock()
        response.json.return_value = {
            "choices": [{"finish_reason": "stop", "message": {"content": boot_text}}],
        }
        post = Mock(return_value=response, side_effect=boot_error)
        boot_screen = Mock(wraps=chat.show_boot)

        with (
            patch.object(chat, "select_memory_user", selected_user),
            patch.object(chat.memory_session, "MemorySession", side_effect=memory),
            patch.object(chat.serial, "Serial", return_value=uart),
            patch.object(chat.time, "sleep"),
            patch.object(chat.Term, "read_key", read_key),
            patch.object(chat, "load_preset", return_value=preset or (
                "Reponds en francais.", "*** 3615 IA ***",
                "Quelle est votre requete ?", "ATTENTE...", None,
            )),
            patch.object(chat, "build_title", return_value=["LOGO"]),
            patch.object(chat, "call_llm", side_effect=reply),
            patch.object(chat, "start_answer", side_effect=start),
            patch.object(chat.mistral_answers, "answer", side_effect=factual_reply),
            patch.dict(chat.os.environ, {"MISTRAL_ANSWER_API": (
                "conversations" if factual_reply else "chat_completions")}),
            patch.object(chat.requests, "post", post),
            patch.object(chat, "MISTRAL_KEY", "test-key" if boot_text or boot_error else ""),
            patch.object(chat, "ANTHROPIC_KEY", ""),
            patch.object(chat, "PROVIDER", "mistral"),
            patch.object(chat, "show_boot", boot_screen),
            patch.object(chat, "get_ip", return_value="192.0.2.1"),
            patch.object(chat.log, "info"),
            self.assertRaises(EndOfInput),
        ):
            chat.run()
        self.boot_requests = post.call_args_list
        self.boot_screens = boot_screen.call_args_list
        self.user_selections = selected_user.call_count
        self.memories = memories
        self.uart = uart
        screens = [screen_text(s) for s in bytes(uart.output).split(bytes([chat.FF])) if s]
        return screens, calls

    def test_no_output_or_generation_until_first_sommaire(self):
        self.run_session(b"ignored" + fn(chat.K_ENVOI))
        self.assertEqual(self.uart.output, b"")
        self.assertEqual(self.boot_screens, [])
        self.assertEqual(self.user_selections, 0)

    def test_french_boot_precedes_language_selection_and_request(self):
        for choice, language in (("en", "en"), ("English", "en"), (" ENGLISH ", "en"),
                                 ("fr", "fr"), ("French", "fr"), ("", "fr"), ("unknown", "fr")):
            with self.subTest(choice=choice):
                screens, calls = self.run_session(
                    login_keys(choice) + b"Calculate 17 x 23" + fn(chat.K_ENVOI))
                self.assertIn("MAINFRAME PRET.", screens[1])
                self.assertIn(chat.BOOT_CONTINUE_PROMPT, screens[1])
                self.assertIn("Choose language", screens[2])
                self.assertIn(chat.UI[language]["question"], screens[3])
                self.assertEqual(self.boot_screens[0].args[1], "fr")
                self.assertEqual(len(calls), 1)
                self.assertIn("Reply in " + ("English" if language == "en" else "French"), calls[0][0])
                self.assertEqual(calls[0][1], [{"role": "user", "content": "Calculate 17 x 23"}])

    def test_suite_continues_boot_in_both_uart_modes(self):
        for suite in (fn(chat.K_SUITE), b"\x1bOn"):
            with self.subTest(suite=suite):
                ignored = b"ignored" + fn(chat.K_ENVOI) + b"\r" + fn(chat.K_RETOUR)
                _, calls = self.run_session(fn(chat.K_SOMMAIRE) + ignored + suite
                    + b"en" + fn(chat.K_ENVOI) + b"Hello" + fn(chat.K_ENVOI))
                self.assertEqual(calls[0][1], [{"role": "user", "content": "Hello"}])

    def test_boot_timeout_continues_login_and_language_timeout_defaults_to_french(self):
        screens, calls = self.run_session([*fn(chat.K_SOMMAIRE), None, None,
                                          *b"Bonjour", *fn(chat.K_ENVOI)])
        self.assertIn("Choose language", screens[2])
        self.assertEqual(len(calls), 1)
        self.assertIn("Reply in French", calls[0][0])
        self.assertEqual(len(self.boot_screens), 1)

    def test_sommaire_and_guide_during_boot_do_not_replay_boot(self):
        for key in (chat.K_SOMMAIRE, chat.K_GUIDE):
            with self.subTest(key=key):
                keys = fn(chat.K_SOMMAIRE) + fn(key)
                if key == chat.K_GUIDE:
                    keys += b"x"
                screens, calls = self.run_session(keys)
                self.assertIn("Choose language", screens[-1])
                self.assertEqual(len(self.boot_screens), 1)
                self.assertEqual(calls, [])

    def test_sommaire_resets_history_but_preserves_user_and_language(self):
        _, calls = self.run_session(login_keys() + b"Hello" + fn(chat.K_ENVOI)
            + fn(chat.K_SOMMAIRE) + b"Again" + fn(chat.K_ENVOI))
        self.assertEqual(self.user_selections, 1)
        self.assertEqual(len(self.boot_screens), 1)
        self.assertEqual(len(self.memories), 2)
        self.assertEqual([m.username for m in self.memories], ["Test", "Test"])
        self.memories[0].close.assert_called()
        self.assertIn("Reply in English", calls[1][0])
        self.assertEqual(calls[1][0].count("SESSION LANGUAGE:"), 1)
        self.assertEqual(calls[1][1], [{"role": "user", "content": "Again"}])

    def test_idle_keeps_user_and_does_not_replay_boot(self):
        _, calls = self.run_session([*login_keys(), None, *b"Hello", *fn(chat.K_ENVOI)])
        self.assertEqual(self.user_selections, 1)
        self.assertEqual(len(self.boot_screens), 1)
        self.assertIn("Reply in English", calls[0][0])

    def test_logout_returns_to_login_and_can_change_language_without_boot(self):
        _, calls = self.run_session(login_keys() + b"LOGOUT" + fn(chat.K_ENVOI)
            + b"fr" + fn(chat.K_ENVOI) + b"Bonjour" + fn(chat.K_ENVOI))
        self.assertEqual(self.user_selections, 2)
        self.assertEqual(len(self.boot_screens), 1)
        self.assertEqual(len(calls), 1)
        self.assertIn("Reply in French", calls[0][0])
        self.assertEqual(calls[0][1], [{"role": "user", "content": "Bonjour"}])

    def test_empty_enter_is_not_a_question_after_login(self):
        _, calls = self.run_session(login_keys("fr") + fn(chat.K_ENVOI) + b"\r"
                                    + b"Bonjour" + fn(chat.K_ENVOI))
        self.assertEqual(calls[0][1], [{"role": "user", "content": "Bonjour"}])

    def test_language_choice_can_be_corrected_with_function_key(self):
        _, calls = self.run_session(fn(chat.K_SOMMAIRE) + fn(chat.K_SUITE)
            + b"ex" + fn(chat.K_CORR) + b"n" + fn(chat.K_ENVOI) + b"Hello" + fn(chat.K_ENVOI))
        self.assertIn("Reply in English", calls[0][0])

    def test_english_pagination_still_requires_french_suite_key(self):
        uart = FakeSerial(fn(chat.K_ENVOI) + fn(chat.K_SUITE))
        term = chat.Term(uart)
        with patch.object(chat.time, "sleep"):
            self.assertEqual(chat.show_response(term, "\n".join(["Answer"] * 19), "en"), 'done')
        self.assertIn("SUITE for the next page", screen_text(uart.output))

    def test_start_screens_fit_without_truncation(self):
        rows = chat.UI["fr"]["boot"]
        self.assertLess(len(rows), chat.SCREEN_ROWS)
        self.assertTrue(all(len(row) < chat.COLS for row in rows))
        self.assertLess(len(chat.BOOT_CONTINUE_PROMPT), chat.COLS)
        uart = FakeSerial([])
        with patch.object(chat.time, "sleep"):
            chat.show_home(chat.Term(uart), ["LOGO"] * chat.TITLE_ART_MAX_HEIGHT, "*** 3615 IA ***")
        self.assertLess(screen_text(uart.output).count("\n"), chat.SCREEN_ROWS)

    def test_generated_startup_is_french_and_separate_from_chat(self):
        narrative = chat.UI["fr"]["boot"][:-2]
        screens, calls = self.run_session(login_keys() + b"Hello" + fn(chat.K_ENVOI),
                                          boot_text="\n".join(narrative))
        request = self.boot_requests[0].kwargs
        self.assertEqual(request["timeout"], chat.BOOT_TIMEOUT)
        messages = request["json"]["messages"]
        self.assertIn("fictional startup", messages[0]["content"])
        self.assertIn("Minitel 722079V", messages[0]["content"])
        self.assertIn("ALCATEL MF-83", messages[1]["content"])
        self.assertIn("French", messages[1]["content"])
        self.assertIn("ECHEC CRITIQUE", messages[1]["content"])
        self.assertIn(chat.BOOT_CONTINUE_PROMPT, screens[1])
        self.assertNotIn("RELAY K7", calls[0][0])
        self.assertEqual(calls[0][1], [{"role": "user", "content": "Hello"}])

    def test_real_default_personality_reaches_chat_with_factual_history_only(self):
        with (TemporaryDirectory() as directory,
              patch.object(chat, "PROMPTS_FILE", Path(directory) / "prompts.json"),
              patch.object(chat, "load_knowledge", return_value="")):
            preset = chat.load_preset()
            for language in ("en", "fr"):
                with self.subTest(language=language):
                    _, calls = self.run_session(login_keys(language)
                        + b"Calculate 17 x 23" + fn(chat.K_ENVOI) + b"Explain" + fn(chat.K_ENVOI),
                        boot_text="MINITEL 722079V -> ALCATEL MF-83\nRELAIS ATELIER: NORMAL\nMAINFRAME PRET.", preset=preset)
                    for identity in ("Minitel 722079V", "ALCATEL MF-83", "Matra Communication Quimper"):
                        self.assertIn(identity, calls[0][0])
                    self.assertIn("CONSCIENCE INTERNE ET PRESENTATION", calls[0][0])
                    self.assertIn("UTILITE ET VERACITE", calls[0][0])
                    self.assertNotIn("RELAIS ATELIER", calls[0][0])
                    self.assertEqual(calls[1][1], [
                        {"role": "user", "content": "Calculate 17 x 23"},
                        {"role": "assistant", "content": "17 x 23 = 391."},
                        {"role": "user", "content": "Explain"}])

    def test_generation_failure_or_invalid_text_falls_back_without_retry(self):
        for settings in ({"boot_error": chat.requests.Timeout("timeout")},
                         {"boot_error": chat.requests.HTTPError("401")},
                         {"boot_text": "   "}, {"boot_text": "```\nBOOT\n```"},
                         {"boot_text": "\n".join(["\x1b[2J"] * 18)}):
            with self.subTest(settings=settings):
                screens, calls = self.run_session(login_keys() + b"Hi" + fn(chat.K_ENVOI), **settings)
                self.assertIn("MAINFRAME PRET.", screens[1])
                self.assertEqual(len(self.boot_requests), 1)
                self.assertEqual(calls[0][1], [{"role": "user", "content": "Hi"}])

    def test_short_and_long_generated_boots_fit_and_restore_page_mode(self):
        for count in (8, 15, 21, 45):
            narrative = [f"CONTROLE {index:02d}: NORMAL" for index in range(count - 1)] + ["MAINFRAME PRET."]
            with self.subTest(count=count):
                self.run_session(fn(chat.K_SOMMAIRE), boot_text="\n".join(narrative))
                screen, scrolling = render_videotex(bytes(self.uart.output))
                self.assertIn("MAINFRAME PRET.", screen)
                self.assertIn(chat.BOOT_CONTINUE_PROMPT, screen)
                if count >= 24:
                    self.assertEqual(screen, (narrative + ["", chat.BOOT_CONTINUE_PROMPT])[-23:] + [""])
                self.assertFalse(scrolling)

    def test_scroll_mode_restored_for_every_boot_exit(self):
        for key, result in ((fn(chat.K_SUITE), "done"), (fn(chat.K_SOMMAIRE), "sommaire"),
                            (fn(chat.K_GUIDE), "guide"), (None, "done")):
            uart = FakeSerial(key or [])
            term = chat.Term(uart)
            with patch.object(chat.time, "sleep"), patch.object(term, "read_key", wraps=term.read_key) as read:
                if key is None:
                    read.return_value = ("timeout", None)
                self.assertEqual(chat.show_boot(term, "fr"), result)
            self.assertTrue(bytes(uart.output).endswith(chat.SCROLL_OFF))

    def test_other_keys_do_not_restart_thirty_second_boot_deadline(self):
        term = Mock()
        now = [0]
        timeouts = []
        def read(timeout):
            timeouts.append(timeout)
            now[0] += 10
            return 'char', ord('x')
        term.read_key.side_effect = read
        with patch.object(chat.time, "monotonic", side_effect=lambda: now[0]):
            self.assertEqual(chat.show_boot(term, "fr"), "done")
        self.assertEqual(timeouts, [30, 20, 10])

    def test_status_acknowledgements_do_not_contaminate_keys_or_chat(self):
        _, calls = self.run_session(fn(chat.K_SOMMAIRE) + b"\x1b\x3a\x73\x42" + fn(chat.K_SUITE)
            + b"en" + fn(chat.K_ENVOI) + b"\x1b\x3a\x71\x40" + b"Hello" + fn(chat.K_ENVOI))
        self.assertEqual(calls[0][1], [{"role": "user", "content": "Hello"}])
        term = chat.Term(Mock())
        term.s.read.side_effect = [b"\x1b", b"", b"\x3a", b"", b"\x71", b"", b"\x40", b"\x13", b"\x48"]
        self.assertEqual(term.read_key(1), ("fn", chat.K_SUITE))
        self.assertTrue(term._presence_seen)


@pytest.mark.parametrize("name,language", [(b"Alice", "en"), (b"", "fr")])
def test_real_user_selection_sommaire_logout_and_shared_identity(tmp_path, monkeypatch, name, language):
    keys = fn(chat.K_SOMMAIRE) + fn(chat.K_SUITE) + name + fn(chat.K_ENVOI)
    if name:
        keys += language.encode() + fn(chat.K_ENVOI)
    keys += fn(chat.K_SOMMAIRE) + b"LOGOUT" + fn(chat.K_ENVOI)
    keys += b"Bob" + fn(chat.K_ENVOI) + b"fr" + fn(chat.K_ENVOI)
    uart = FakeSerial(keys)
    memories = []
    original = chat.memory_session.MemorySession

    def create(username):
        memory = original(username, root=tmp_path)
        memories.append(memory)
        return memory

    monkeypatch.setattr(chat.memory_session, "MemorySession", create)
    monkeypatch.setattr(chat.time, "sleep", Mock())
    monkeypatch.setattr(chat, "MISTRAL_KEY", "")
    monkeypatch.setattr(chat, "ANTHROPIC_KEY", "")
    monkeypatch.setattr(chat, "load_preset", lambda: ("persona", "3615 IA", "Question?", "WAIT", None))
    monkeypatch.setattr(chat, "build_title", lambda _: ["LOGO"])
    monkeypatch.setattr(chat, "factual_knowledge", lambda: "")
    monkeypatch.setattr(chat.gmail_mail, "enabled", lambda: False)
    boot = Mock(wraps=chat.show_boot)
    monkeypatch.setattr(chat, "show_boot", boot)
    with pytest.raises(EndOfInput):
        chat.run(chat.Term(uart))
    assert len(memories) == 3
    assert memories[0].key == memories[1].key
    assert memories[0].username == memories[1].username
    assert memories[0].key == ("shared" if not name else chat.memory_session.memory.user_key("Alice")[0])
    assert memories[2].username == "Bob"
    assert memories[2].key != memories[0].key
    assert all(memory.closed for memory in memories)
    boot.assert_called_once()
    assert boot.call_args.args[1] == "fr"
    output = screen_text(uart.output)
    assert output.index("MAINFRAME PRET.") < output.index("Nom / name")
    assert output.count("Nom / name") == 2


if __name__ == "__main__":
    unittest.main()
