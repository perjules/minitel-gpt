"""Check byte-level display parity and the real session loop without hardware."""

import curses
import unittest
from collections import deque
from queue import Queue
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from test_terminal_startup import render_videotex

from services import minitel_chatgpt as chat
from services.console_terminal import ConsoleSerial, Screen, key_bytes


class EndOfInput(Exception):
    pass


class Window:
    def __init__(self, keys=()):
        self.keys = deque(keys)

    def get_wch(self):
        if not self.keys:
            raise EndOfInput
        return self.keys.popleft()

    def getmaxyx(self):
        return 26, 80

    def __getattr__(self, name):
        return lambda *args: None


class ConsoleTests(unittest.TestCase):
    def test_output_matches_existing_videotex_model_even_with_split_controls(self):
        screen = Screen()
        data = (bytes([chat.FF, chat.RS]) + chat.FG_WHITE
                + b"A" * 40 + b"\r\n" + chat.SCROLL_ON
                + b"".join(f"row {i}\r\n".encode() for i in range(28))
                + chat.SCROLL_OFF + chat.FG_GREEN + b"> ")
        for code in data:
            screen.write(bytes([code]))
        expected, scrolling = render_videotex(data)
        self.assertEqual(["".join(row).rstrip() for row in screen.rows],
                         [row.rstrip() for row in expected])
        self.assertEqual(screen.scrolling, scrolling)
        self.assertIn("row 27", "\n".join(expected))

    def test_keys_use_real_decoder_local_echo_and_destructive_backspace(self):
        console = ConsoleSerial(Window(["a", "b", curses.KEY_BACKSPACE, "\n", "\x0e", "\x10", "\x07", "\x12"]))
        term = chat.Term(console)
        text, action = chat.read_question(term)
        self.assertEqual((text, action), ("a", "envoi"))
        self.assertEqual("".join(console.screen.rows[0][:4]), "> a ")
        for code in (chat.K_SUITE, chat.K_SOMMAIRE, chat.K_GUIDE, chat.K_RETOUR):
            self.assertEqual(term.read_key(1), ("fn", code))

    def test_quit_and_unknown_keys(self):
        for key in ("\x03", "\x11"):
            with self.assertRaises(KeyboardInterrupt):
                key_bytes(key)
        self.assertEqual(key_bytes(curses.KEY_UP), b"")
        self.assertEqual(key_bytes("é"), b"")

    def test_real_loop_startup_paging_history_calculator_and_reset(self):
        # Blank username -> shared scope -> English -> SUITE. Page a long reply,
        # ask a follow-up, use local arithmetic, then reset to username selection.
        keys = (list("\nen\n") + ["\x0e"] + list("hello\n")
                + ["\x0e"] + list("follow up\nCALC 2+3\n") + ["\x10"])
        console = ConsoleSerial(Window(keys))
        snapshots = []
        original_write = console.write

        def capture(data):
            original_write(data)
            snapshots.append("\n".join("".join(row) for row in console.screen.rows))

        console.write = capture
        calls = []

        def answer(history, *args, **kwargs):
            calls.append([dict(turn) for turn in history])
            text = "\n".join(["answer"] * 19) if len(calls) == 1 else "follow-up result"
            return {"status": "ok", "text": text,
                    "chunks": [{"type": "text", "text": text}], "references": []}

        def start(system, history, language, options, knowledge, **kwargs):
            result = chat.call_answer(system, history, language, options, knowledge, **kwargs)
            output = Queue()
            output.put((result, None))
            return Mock(is_alive=lambda: False), output, chat.time.monotonic()

        with (
            TemporaryDirectory() as root,
            patch.dict("os.environ", {"MINITEL_MEMORY_DIR": root, "GMAIL_ENABLED": "0",
                                      "MISTRAL_ANSWER_API": "conversations"}),
            patch.object(chat.serial, "Serial", side_effect=AssertionError("UART opened")),
            patch.object(chat.time, "sleep"),
            patch.object(chat, "PROVIDER", "mistral"),
            patch.object(chat, "prepare_boot", return_value=["BOOT TEST"]),
            patch.object(chat, "build_title", return_value=["LOGO"]),
            patch.object(chat, "start_answer", side_effect=start),
            patch.object(chat.mistral_answers, "answer", side_effect=answer),
            self.assertRaises(EndOfInput),
        ):
            chat.run(chat.Term(console))
        self.assertEqual(len(calls), 2)
        self.assertEqual([turn["role"] for turn in calls[1]], ["user", "assistant", "user"])
        self.assertEqual(calls[1][0]["content"], "hello")
        self.assertEqual(calls[1][-1]["content"], "follow up")
        visible = "\n".join(snapshots)
        self.assertIn("BOOT TEST", visible)
        self.assertTrue(any(chat.UI["en"]["next_page"] in screen for screen in snapshots))
        self.assertIn("2+3", visible)
        self.assertIn("5", visible)
        self.assertIn("Nom / name", snapshots[-1])


if __name__ == "__main__":
    unittest.main()
