"""Exercise startup through the real UART key decoder and conversation loop."""
import unittest
from collections import deque
from unittest.mock import patch

from services import minitel_chatgpt as chat


class EndOfInput(Exception):
    pass


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
            raise TimeoutError
        self.consumed.append(key)
        return bytes([key])

    def write(self, data):
        self.output.extend(data)


def fn(code):
    return bytes([chat.SEP, code])


def screen_text(data):
    """Strip colour/cursor controls, retaining line breaks and visible text."""
    visible = bytearray()
    skip = False
    for code in data:
        if skip:
            skip = False
        elif code == chat.ESC:
            skip = True
        elif code in (chat.CR, chat.LF) or 0x20 <= code <= 0x7E:
            visible.append(code)
    return visible.decode("ascii")


class StartupTests(unittest.TestCase):
    def run_session(self, keys):
        uart = FakeSerial(keys)
        calls = []

        def reply(system, history):
            calls.append((system, [dict(turn) for turn in history], bytes(uart.consumed)))
            return "17 x 23 = 391."

        real_read_key = chat.Term.read_key

        def read_key(term, timeout):
            try:
                return real_read_key(term, timeout)
            except TimeoutError:
                return 'timeout', None

        with (
            patch.object(chat.serial, "Serial", return_value=uart),
            patch.object(chat.time, "sleep"),
            patch.object(chat.Term, "read_key", read_key),
            patch.object(chat, "load_preset", return_value=(
                "Reponds en francais.", "*** 3615 IA ***",
                "Quelle est votre requete ?", "ATTENTE...", None,
            )),
            patch.object(chat, "build_title", return_value=["LOGO"]),
            patch.object(chat, "call_llm", side_effect=reply),
            patch.object(chat, "get_ip", return_value="192.0.2.1"),
            patch.object(chat.log, "info"),
            self.assertRaises(EndOfInput),
        ):
            chat.run()
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
        screens, calls = self.run_session([None, *fn(chat.K_SUITE), *b"Hi", *fn(chat.K_ENVOI)])
        self.assertIn("MAINFRAME PRET.", screens[1])
        self.assertEqual(len(calls), 1)
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


if __name__ == "__main__":
    unittest.main()
