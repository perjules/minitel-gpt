"""Exercise startup through the real UART key decoder and conversation loop."""
import unittest
from collections import deque
from pathlib import Path
from queue import Queue
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

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


class StartupTests(unittest.TestCase):
    def run_session(self, keys, boot_text=None, boot_error=None, preset=None,
                    factual_reply=None):
        uart = FakeSerial(keys)
        calls = []

        def reply(system, history):
            calls.append((system, [dict(turn) for turn in history], bytes(uart.consumed)))
            return "17 x 23 = 391."

        def start(system, history, language, options, knowledge, **kwargs):
            # UART fixtures contain already queued future input: complete the
            # mocked request before reading those keys. Worker races/reset are
            # exercised separately with a real blocked thread.
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
            patch.object(chat, "select_memory_user", return_value=(Mock(take_warning=lambda: "", context=lambda q: "", interested=lambda q: False), "envoi")),
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
        self.uart = uart
        screens = [screen_text(s) for s in bytes(uart.output).split(bytes([chat.FF])) if s]
        return screens, calls

    def test_language_choices_and_silent_french_default(self):
        for choice, language in (
            ("en", "en"), ("English", "en"), (" ENGLISH ", "en"),
            ("fr", "fr"), ("French", "fr"), (" FR ", "fr"),
            ("", "fr"), ("unknown", "fr"),
        ):
            with self.subTest(choice=choice):
                screens, calls = self.run_session(
                    choice.encode() + fn(chat.K_ENVOI) + fn(chat.K_SUITE)
                    + b"Calculate 17 x 23" + fn(chat.K_ENVOI) + fn(chat.K_SOMMAIRE)
                )
                self.assertIn("Choose language", screens[0])
                self.assertNotIn("requete", screens[0])
                self.assertIn(chat.UI[language]["boot"][-1], screens[1])
                self.assertIn(chat.UI[language]["question"], screens[2])
                self.assertEqual(len(calls), 1)
                system, history, _ = calls[0]
                self.assertIn("Reply in " + ("English" if language == "en" else "French"), system)
                self.assertEqual(history, [{"role": "user", "content": "Calculate 17 x 23"}])
                self.assertIn("SOMMAIRE", screens[3])

    def test_only_suite_continues_boot_in_both_uart_modes(self):
        for suite in (fn(chat.K_SUITE), b"\x1bOn"):
            with self.subTest(suite=suite):
                ignored = b"ignored" + fn(chat.K_ENVOI) + b"\r" + fn(chat.K_RETOUR)
                screens, calls = self.run_session(
                    b"en" + fn(chat.K_ENVOI) + ignored + suite + b"Hello" + fn(chat.K_ENVOI)
                )
                self.assertEqual(len(calls), 1)
                self.assertIn(suite, calls[0][2])
                self.assertEqual(calls[0][1][0]["content"], "Hello")
                self.assertIn("MAINFRAME READY.", screens[1])

    def test_language_timeout_defaults_to_french_but_boot_timeout_returns_home(self):
        screens, calls = self.run_session(
            [None, *fn(chat.K_SUITE), *b"Hi", *fn(chat.K_ENVOI)],
            boot_text="\n".join(chat.UI["fr"]["boot"][:-2]),
        )
        self.assertIn("MAINFRAME PRET.", screens[1])
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.boot_requests, [])
        screens, calls = self.run_session([*fn(chat.K_ENVOI), None])
        self.assertIn("Choose language", screens[-1])
        self.assertEqual(calls, [])

    def test_sommaire_or_guide_during_boot_does_not_open_chat(self):
        for key in (chat.K_SOMMAIRE, chat.K_GUIDE):
            with self.subTest(key=key):
                keys = b"en" + fn(chat.K_ENVOI) + fn(key)
                if key == chat.K_GUIDE:
                    keys += b"x"  # return from the existing guide screen
                screens, calls = self.run_session(keys)
                self.assertIn("Choose language", screens[-1])
                self.assertFalse(any("What is your request?" in s for s in screens))
                self.assertEqual(calls, [])

    def test_language_and_history_reset_on_sommaire(self):
        _, calls = self.run_session(
            b"en" + fn(chat.K_ENVOI) + fn(chat.K_SUITE) + b"Hello" + fn(chat.K_ENVOI)
            + fn(chat.K_SOMMAIRE) + b"French" + fn(chat.K_ENVOI) + fn(chat.K_SUITE)
            + b"Bonjour" + fn(chat.K_ENVOI)
        )
        self.assertEqual(len(calls), 2)
        self.assertIn("Reply in English", calls[0][0])
        self.assertIn("Reply in French", calls[1][0])
        self.assertEqual(calls[1][0].count("SESSION LANGUAGE:"), 1)
        self.assertEqual(calls[1][1], [{"role": "user", "content": "Bonjour"}])

    def test_empty_enter_is_not_a_question_after_boot(self):
        _, calls = self.run_session(
            b"\r" + fn(chat.K_SUITE) + fn(chat.K_ENVOI) + b"\r"
            + b"Bonjour" + fn(chat.K_ENVOI)
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1][0]["content"], "Bonjour")

    def test_language_choice_can_be_corrected_with_function_key(self):
        _, calls = self.run_session(
            b"ex" + fn(chat.K_CORR) + b"n" + fn(chat.K_ENVOI)
            + fn(chat.K_SUITE) + b"Hello" + fn(chat.K_ENVOI)
        )
        self.assertIn("Reply in English", calls[0][0])

    def test_english_pagination_still_requires_french_suite_key(self):
        uart = FakeSerial(fn(chat.K_ENVOI) + fn(chat.K_SUITE))
        term = object.__new__(chat.Term)
        term.s = uart
        with patch.object(chat.time, "sleep"):
            result = chat.show_response(term, "\n".join(["Answer"] * 19), "en")
        self.assertEqual(result, 'done')
        self.assertIn("SUITE for the next page", screen_text(uart.output))
        self.assertEqual(bytes(uart.consumed), fn(chat.K_ENVOI) + fn(chat.K_SUITE))

    def test_start_screens_fit_without_truncation(self):
        for language in ("en", "fr"):
            rows = chat.UI[language]["boot"]
            self.assertGreaterEqual(len(rows), 18)
            self.assertLess(len(rows), chat.SCREEN_ROWS)
            for row in rows:
                self.assertEqual(row, chat.to_ascii(row))
                self.assertLess(len(row), chat.COLS)  # avoid a 40th-column auto-wrap
            screens, _ = self.run_session(language.encode() + fn(chat.K_ENVOI))
            self.assertEqual(screens[1].splitlines(), rows)
        # Worst-case supported title plus the menu and its input cursor.
        uart = FakeSerial([])
        term = object.__new__(chat.Term)
        term.s = uart
        with patch.object(chat.time, "sleep"):
            chat.show_home(term, ["LOGO"] * chat.TITLE_ART_MAX_HEIGHT, "*** 3615 IA ***")
        self.assertLess(screen_text(uart.output).count("\n"), chat.SCREEN_ROWS)

    def test_generated_startup_uses_its_own_request_and_python_suite_footer(self):
        for language in ("en", "fr"):
            with self.subTest(language=language):
                # The existing reviewed-style screen is also a valid generated fixture.
                narrative = chat.UI[language]["boot"][:-2]
                screens, calls = self.run_session(
                    language.encode() + fn(chat.K_ENVOI) + fn(chat.K_SUITE)
                    + b"Hello" + fn(chat.K_ENVOI), boot_text="\n".join(narrative),
                )
                self.assertEqual(len(self.boot_requests), 1)
                request = self.boot_requests[0].kwargs
                self.assertEqual(request["timeout"], chat.BOOT_TIMEOUT)
                messages = request["json"]["messages"]
                self.assertEqual([m["role"] for m in messages], ["system", "user"])
                self.assertIn("fictional startup", messages[0]["content"])
                self.assertIn("Minitel 722079V", messages[0]["content"])
                self.assertIn("ALCATEL MF-83", messages[0]["content"])
                self.assertIn("ALCATEL MF-83", messages[1]["content"])
                self.assertIn("English" if language == "en" else "French", messages[1]["content"])
                self.assertIn("CRITICAL FAIL" if language == "en" else "ECHEC CRITIQUE",
                              messages[1]["content"])
                expected = narrative + ["", chat.UI[language]["boot"][-1]]
                self.assertEqual(self.boot_screens[0].args[2], expected)
                self.assertEqual(screens[1].splitlines(), expected)
                self.assertEqual(calls[0][1], [{"role": "user", "content": "Hello"}])
                self.assertNotIn("RELAY K7", calls[0][0])
                self.assertIn("SUITE", screens[1].splitlines()[-1])

    def test_real_default_personality_reaches_chat_with_factual_history_only(self):
        # Exercise the real config loader on a fresh installation, then the UART flow.
        with (
            TemporaryDirectory() as directory,
            patch.object(chat, "PROMPTS_FILE", Path(directory) / "prompts.json"),
            patch.object(chat, "load_knowledge", return_value=""),
        ):
            preset = chat.load_preset()
            for language in ("en", "fr"):
                with self.subTest(language=language):
                    boot = "MINITEL 722079V -> ALCATEL MF-83\nRELAY WORKSHOP: NOMINAL\nMAINFRAME READY."
                    screens, calls = self.run_session(
                        language.encode() + fn(chat.K_ENVOI) + fn(chat.K_SUITE)
                        + b"Calculate 17 x 23" + fn(chat.K_ENVOI)
                        + b"Explain" + fn(chat.K_ENVOI),
                        boot_text=boot, preset=preset,
                    )
                    system = calls[0][0]
                    for identity in ("Minitel 722079V", "ALCATEL MF-83", "Matra Communication Quimper"):
                        self.assertIn(identity, system)
                    self.assertIn("CONSCIENCE INTERNE ET PRESENTATION", system)
                    self.assertIn("UTILITE ET VERACITE", system)
                    self.assertIn("Reply in " + ("English" if language == "en" else "French"), system)
                    self.assertNotIn("RELAY WORKSHOP", system)
                    self.assertEqual(calls[1][1], [
                        {"role": "user", "content": "Calculate 17 x 23"},
                        {"role": "assistant", "content": "17 x 23 = 391."},
                        {"role": "user", "content": "Explain"},
                    ])
                    self.assertIn("17 x 23 = 391.", "\n".join(screens))

    def test_generation_failure_or_invalid_text_falls_back_without_retry(self):
        for settings in (
            {"boot_error": chat.requests.Timeout("timeout")},
            {"boot_error": chat.requests.HTTPError("401")},
            {"boot_text": "   "},
            {"boot_text": "```\nBOOT\n```"},
            {"boot_text": "\n".join(["\x1b[2J"] * 18)},
        ):
            with self.subTest(settings=settings):
                screens, calls = self.run_session(
                    b"en" + fn(chat.K_ENVOI) + fn(chat.K_SUITE) + b"Hi" + fn(chat.K_ENVOI),
                    **settings,
                )
                self.assertEqual(screens[1].splitlines(), chat.UI["en"]["boot"])
                self.assertEqual(len(self.boot_requests), 1)
                self.assertEqual(calls[0][1], [{"role": "user", "content": "Hi"}])

    def test_short_and_long_api_boots_do_not_fall_back(self):
        for count in (8, 15, 21, 45):
            narrative = [f"CHECK {index:02d}: NOMINAL" for index in range(count - 1)]
            narrative.append("MAINFRAME READY.")
            with self.subTest(count=count):
                screens, _ = self.run_session(
                    b"en" + fn(chat.K_ENVOI), boot_text="\n".join(narrative),
                )
                expected = narrative + ["", chat.UI["en"]["boot"][-1]]
                self.assertEqual(screens[1].splitlines(), expected)
                self.assertEqual(len(self.boot_requests), 1)
                screen, scrolling = render_videotex(bytes(self.uart.output))
                self.assertIn("MAINFRAME READY.", screen)
                self.assertIn(chat.UI["en"]["boot"][-1], screen)
                if count >= 24:
                    self.assertEqual(screen, expected[-23:] + [""])
                self.assertFalse(scrolling)  # also restored when input/IO raises

    def test_scroll_mode_is_restored_for_every_boot_exit(self):
        for key, result in (
            (fn(chat.K_SUITE), "done"), (fn(chat.K_SOMMAIRE), "sommaire"),
            (fn(chat.K_GUIDE), "guide"), (None, "timeout"),
        ):
            with self.subTest(result=result):
                uart = FakeSerial(key or [])
                term = object.__new__(chat.Term)
                term.s = uart
                with (
                    patch.object(chat.time, "sleep"),
                    patch.object(term, "read_key", wraps=term.read_key) as read_key,
                ):
                    if key is None:
                        read_key.return_value = ("timeout", None)
                    self.assertEqual(chat.show_boot(term, "en"), result)
                output = bytes(uart.output)
                self.assertIn(b"\x1b\x3a\x69\x43", output)
                self.assertTrue(output.endswith(b"\x1b\x3a\x6a\x43"))
                _, scrolling = render_videotex(output)
                self.assertFalse(scrolling)

    def test_scroll_acknowledgements_do_not_contaminate_keys_or_chat(self):
        _, calls = self.run_session(
            b"en" + fn(chat.K_ENVOI) + b"\x1b\x3a\x73\x42"
            + fn(chat.K_SUITE) + b"\x1b\x3a\x73\x40"
            + b"Hello" + fn(chat.K_ENVOI),
        )
        self.assertEqual(calls[0][1], [{"role": "user", "content": "Hello"}])
        # A UART read may pause between bytes of the terminal's reply.
        term = object.__new__(chat.Term)
        term.s = Mock()
        term.s.read.side_effect = [b"\x1b", b"", b"\x3a", b"", b"\x73", b"", b"\x42",
                                  b"\x13", b"\x48"]
        self.assertEqual(term.read_key(1), ("fn", chat.K_SUITE))


if __name__ == "__main__":
    unittest.main()
