"""Real worker cancellation, deadline isolation and UART recovery."""
import json
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import test_terminal_startup as startup
from test_terminal_startup import FakeSerial, fn, screen_text

from services import minitel_chatgpt as chat
from services import mistral_answers as engine


class WaitingTests(unittest.TestCase):
    def test_sommaire_discards_late_response_and_next_worker_has_own_queue(self):
        release = threading.Event()
        entered = threading.Event()
        history = [{"role": "user", "content": "Old question"}]

        def blocked(*args):
            entered.set()
            release.wait(2)
            return {"display": "STALE", "turn": {"role": "assistant", "content": "STALE"}}

        with patch.object(chat, "call_answer", side_effect=blocked):
            pending = chat.start_answer("", history, "en", ("conversations", True, 60), "")
            self.assertTrue(entered.wait(1))
            term = object.__new__(chat.Term)
            term.s = FakeSerial(fn(chat.K_SOMMAIRE))
            self.assertEqual(chat.wait_answer(term, pending, 60), (None, "sommaire"))
            self.assertTrue(pending[0].is_alive())
            release.set()
            pending[0].join(1)
            self.assertFalse(pending[0].is_alive())
        self.assertEqual(history, [{"role": "user", "content": "Old question"}])
        self.assertEqual(term.s.output, b"")
        with patch.object(chat, "call_answer", return_value={"display": "FRESH", "turn": None}):
            fresh = chat.start_answer("", [], "fr", ("conversations", True, 60), "")
            fresh[0].join(1)
            result, action = chat.wait_answer(Mock(), fresh, 60)
        self.assertEqual(action, "done")
        self.assertEqual(result["display"], "FRESH")
        self.assertEqual(pending[1].get_nowait()[0]["display"], "STALE")

    def test_total_deadline_does_not_accept_late_result_or_retry(self):
        release = threading.Event()
        with patch.object(chat, "call_answer", side_effect=lambda *args: release.wait(1)) as call:
            pending = chat.start_answer("", [], "fr", ("conversations", True, 1), "")
            term = Mock()
            term.read_key.return_value = ("timeout", None)
            with patch.object(chat.time, "monotonic", return_value=pending[2] + 2):
                self.assertEqual(chat.wait_answer(term, pending, 1), (None, "deadline"))
            term.read_key.assert_not_called()
            call.assert_called_once()
            release.set()
            pending[0].join(1)

    def test_run_reset_with_blocked_request_prevents_overlap_and_stale_display(self):
        release = threading.Event()
        entered = threading.Event()
        uart = FakeSerial(
            b"en" + fn(chat.K_ENVOI) + fn(chat.K_SUITE) + b"Old" + fn(chat.K_ENVOI)
            + fn(chat.K_SOMMAIRE) + b"fr" + fn(chat.K_ENVOI) + fn(chat.K_SUITE)
            + b"New" + fn(chat.K_ENVOI)
        )
        real_start = chat.start_answer
        pending_requests = []

        def blocked(*args):
            entered.set()
            release.wait(2)
            return {"display": "STALE", "turn": None}

        def start(*args):
            pending = real_start(*args)
            pending_requests.append(pending)
            self.assertTrue(entered.wait(1))
            return pending

        try:
            with (patch.object(chat.serial, "Serial", return_value=uart),
                  patch.object(chat.time, "sleep"),
                  patch.object(chat, "load_preset", return_value=("persona", "3615 IA", "Question?", "WAIT", None)),
                  patch.object(chat, "factual_knowledge", return_value=""),
                  patch.object(chat, "build_title", return_value=["LOGO"]),
                  patch.object(chat, "prepare_boot", side_effect=lambda lang: chat.UI[lang]["boot"]),
                  patch.object(chat, "call_answer", side_effect=blocked) as call,
                  patch.object(chat, "start_answer", side_effect=start),
                  self.assertRaises(startup.EndOfInput)):
                chat.run()
            call.assert_called_once()
            self.assertNotIn("STALE", screen_text(uart.output))
            self.assertIn("Requete precedente", screen_text(uart.output))
        finally:
            release.set()
            for pending in pending_requests:
                pending[0].join(1)


class FactualIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.flow = startup.StartupTests()
        self.data = json.loads((Path(__file__).parent / "fixtures" / "conversations_searched.json").read_text())

    def test_neutral_turn_source_followup_reset_and_theatrical_identity(self):
        calls = []
        result = engine.parse_response(self.data)

        def answer(history, language, key, model, web_search, timeout, knowledge):
            calls.append((history, language, knowledge))
            return result

        keys = (b"en" + fn(chat.K_ENVOI) + fn(chat.K_SUITE) + b"Search now" + fn(chat.K_ENVOI)
                + b"Second source?" + fn(chat.K_ENVOI) + fn(chat.K_SOMMAIRE)
                + b"fr" + fn(chat.K_ENVOI) + fn(chat.K_SUITE) + b"Bonjour" + fn(chat.K_ENVOI))
        with patch.object(chat, "factual_knowledge", return_value="Known user reference"):
            screens, legacy = self.flow.run_session(keys, factual_reply=answer)
        self.assertEqual(legacy, [])
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[0][0], [{"role": "user", "content": "Search now"}])
        turn = calls[1][0][1]
        self.assertEqual(turn["content"], result["text"])
        self.assertEqual(turn["references"], result["references"])
        self.assertNotIn("Sources:", turn["content"])
        self.assertNotIn("ALCATEL", json.dumps(calls))
        self.assertEqual(calls[2][0], [{"role": "user", "content": "Bonjour"}])
        self.assertEqual(calls[2][1], "fr")
        output = "\n".join(screens)
        self.assertIn("MINITEL 722079V -> ALCATEL MF-83", output)
        self.assertIn("[2] example.net", output)
        self.assertIn("17.25", output)

    def test_failed_question_and_notice_do_not_enter_next_factual_request(self):
        for failure in (RuntimeError("connection"), "search_not_executed", "search_incomplete", "insufficient_sources"):
            calls = []

            def answer(history, *args, calls=calls, failure=failure):
                calls.append(history)
                if len(calls) == 1:
                    if isinstance(failure, Exception):
                        raise failure
                    result = engine.parse_response(self.data)
                    result["status"] = failure
                    return result
                return engine.parse_response({"outputs": [{"type": "message.output", "content": "Recovered."}]})

            with self.subTest(failure=failure):
                screens, _ = self.flow.run_session(
                    b"en" + fn(chat.K_ENVOI) + fn(chat.K_SUITE) + b"Search now" + fn(chat.K_ENVOI)
                    + b"Explain" + fn(chat.K_ENVOI), factual_reply=answer)
                self.assertEqual(calls[1], [{"role": "user", "content": "Explain"}])
                self.assertIn("Recovered.", "\n".join(screens))
                self.assertNotIn("17.25", "\n".join(screens))


if __name__ == "__main__":
    unittest.main()
