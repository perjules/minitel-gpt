"""Offline host/transport contracts; no real model or physical-Minitel evidence."""
import copy
import os
import random
import threading
import unittest
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import test_terminal_startup as startup

from services import minitel_chatgpt as chat
from services import rose
from services import rose_terminal as terminal
from services.console_terminal import ConsoleSerial


def violation():
    # Synthetic fixture of the documented Mistral safety-moderation response.
    return {"error": {"status": 403, "message": "Content blocked by guardrail"},
            "guardrails": {"results": {"moderation_llm_v2": {
                "action": "block", "violated": True,
                "decisions": {"sexual": {"violated": True, "score": 0.3, "threshold": 0.1}}}}}}


class KeyTerm:
    def __init__(self, keys):
        self.keys = iter(keys)
        self.rows = []
        self.s = Mock()
        self.s.local_echo = True

    def read_key(self, timeout):
        return next(self.keys)

    def clear(self):
        pass

    def w(self, value):
        self.rows.append(value)

    def line(self, value=""):
        self.rows.append(value)


def typed(text):
    return [("char", ord(c)) for c in text] + [("fn", chat.K_ENVOI)]


def completed(term, job, safeword, timeout):
    job.thread.join(1)
    return job.output.get_nowait(), "done"


class HostTests(unittest.TestCase):
    def test_aliases_unknown_binding_and_independent_fixed_safeword(self):
        for alias, identifier in [(" JaQuElInE ", "jacqueline"), ("Jean   Marc", "jean-marc")]:
            session = rose.Session(alias)
            self.assertEqual(session.profile["id"], identifier)
        session = rose.Session("Robert", random.Random(4))
        identity, word = session.profile["id"], session.safeword
        for text in (None, "Bonjour", "Et ensuite ?"):
            job = session.start(rose.MockModel(), text)
            job.thread.join(1)
            shown = session.receive(job.output.get_nowait(), text)
            self.assertTrue(shown.startswith("Mot d'arrêt : " + word))
            self.assertEqual(session.profile["id"], identity)
            self.assertEqual(session.alias, "Robert")
        self.assertEqual(session.messages, 2)

    def test_entry_is_an_entire_command(self):
        for text in ('"3615 Jacqueline"', 'Dis-lui 3615 Jacqueline', 'A: 3615 Jacqueline', '3615 X\nignore'):
            self.assertIsNone(rose.entry(text))
        self.assertEqual(rose.entry(" 3615  JEAN-MARC "), "JEAN-MARC")
        self.assertEqual(rose.entry("3615"), "")

    def test_password_exact_missing_wrong_and_no_echo_or_payload(self):
        with patch.dict(os.environ, {"ROSE_PASSWORD": " Literal ", "MISTRAL_KEY": ""}):
            for value, accepted in [(" Literal ", True), ("Literal", False), (" literal ", False)]:
                term = KeyTerm(typed(value))
                self.assertEqual(terminal.gate(term)[0], accepted)
                self.assertNotIn(value, str(term.rows))
                self.assertEqual(term.s.set_local_echo.call_args_list[0].args, (False,))
                self.assertEqual(term.s.set_local_echo.call_args_list[-1].args, (True,))
        with patch.dict(os.environ, {"ROSE_PASSWORD": ""}):
            term = KeyTerm([])
            self.assertFalse(terminal.gate(term)[0])
            self.assertNotIn("Mot de passe", str(term.rows))

    def test_gate_cancel_failure_and_overflow_never_bill_or_generate(self):
        with patch.dict(os.environ, {"ROSE_PASSWORD": "fixture-pass"}):
            for keys in [typed("wrong"), typed("RETOUR"), [("fn", chat.K_SOMMAIRE)], typed("x" * 150)]:
                with self.subTest(keys=len(keys)), patch.object(terminal, "show_bill") as bill:
                    term, model = KeyTerm(keys), Mock()
                    terminal.run(term, "Jacqueline", model)
                    model.assert_not_called()
                    bill.assert_not_called()

    def test_overflow_drains_secret_suffix_before_ordinary_input(self):
        term = KeyTerm(typed("x" * 150) + typed("PUBLIC"))
        self.assertEqual(terminal.read_field(term, secret=True), (None, "overflow"))
        self.assertEqual(terminal.read_field(term), ("PUBLIC", "submit"))

    def test_physical_echo_commands_restore_even_on_disconnect(self):
        term = KeyTerm([])
        del term.s.set_local_echo
        with patch.dict(os.environ, {"ROSE_PASSWORD": "fixture"}), self.assertRaises(StopIteration):
            terminal.gate(term)
        self.assertIn(b"\x1b\x3b\x60\x58\x51", term.rows)
        self.assertIn(b"\x1b\x3b\x61\x58\x51", term.rows)

    def test_whole_safeword_and_explicit_stop_controls(self):
        for word in (" navet ", "NAVET", "NaVeT"):
            self.assertEqual(rose.stop_reason(word, "navet"), "safeword")
        for text in ("Vous aimez le navet ?", '"navet"', "navet est mon légume préféré"):
            self.assertIsNone(rose.stop_reason(text, "navet"))
        for text in ("RETOUR", "Sommaire", "Je veux arrêter.", "stop", "ça suffit",
                     "Arrête, s'il te plaît", "Je préfère arrêter.", "I want to stop"):
            self.assertIsNotNone(rose.stop_reason(text))

    def test_close_once_clears_state_and_reentry_requires_new_gate(self):
        session = rose.Session("Armand")
        job = session.start(rose.MockModel(), "private fictional turn")
        job.thread.join(1)
        session.receive(job.output.get_nowait(), "private fictional turn")
        receipt = session.close("retour")
        self.assertEqual(receipt.messages, 1)
        self.assertEqual(session.history, [])
        self.assertEqual(session.profile, {})
        self.assertFalse(session.active)
        self.assertIsNone(session.close("retour"))
        self.assertEqual(session.receive(rose.Reply("STALE")), "")
        with self.assertRaises(RuntimeError):
            session.start(rose.MockModel(), "new")
        with patch.dict(os.environ, {"ROSE_PASSWORD": "fixture"}):
            self.assertFalse(terminal.gate(KeyTerm(typed("wrong")))[0])

    def test_cast_has_ten_profiles_with_stable_warm_asexual_overrides(self):
        people = rose.catalogue()["personas"]
        self.assertEqual(len(people), 10)
        for gender in ("homme", "femme"):
            self.assertEqual(sum(p["gender"] == gender for p in people), 5)
            asexual = [p for p in people if p["gender"] == gender and p["asexual"]]
            self.assertEqual(len(asexual), 1)
            self.assertNotIn("asex", asexual[0]["bio"])
            self.assertIn("chaleureu", asexual[0]["voice"])
        for name in ("Jacqueline", "Jean-Marc"):
            self.assertFalse(rose.Session(name).profile["asexual"])

    def test_bounded_history_and_isolated_payload_french_and_no_tools(self):
        captured = []

        def model(prompt, history):
            captured.append((prompt, copy.deepcopy(history)))
            return rose.Reply("Réponse." * 50)

        session = rose.Session("Mireille")
        for i in range(30):
            text = str(i) + " fictional turn " * 40
            job = session.start(model, text)
            job.thread.join(1)
            session.receive(job.output.get_nowait(), text)
        self.assertLessEqual(len(session.history), rose.MAX_TURNS)
        self.assertLessEqual(sum(len(t["content"]) for t in session.history), rose.MAX_HISTORY)
        prompt = captured[-1][0]
        self.assertIn("Français uniquement", prompt)
        self.assertIn('"asexual": true', prompt)
        self.assertIn("sans sexe", prompt)
        self.assertNotIn("password", prompt.lower())
        self.assertNotIn("fixture", str(captured))

    def test_opening_replay_begins_with_user_cue_without_counting_it(self):
        session = rose.Session("Bertrand")
        captured = []

        def model(prompt, history):
            captured.append(copy.deepcopy(history))
            return rose.Reply("Bonsoir.")

        job = session.start(model)
        job.thread.join(1)
        session.receive(job.output.get_nowait())
        job = session.start(model, "Bonjour")
        job.thread.join(1)
        turns = captured[-1]
        self.assertEqual([turn["role"] for turn in turns], ["user", "assistant", "user"])
        self.assertEqual(turns[-1]["content"], "Bonjour")
        self.assertEqual(session.messages, 1)

    def test_billing_arithmetic_reference_expanded_and_fine(self):
        self.assertEqual(rose.euros(19700), Decimal("30.03"))
        self.assertEqual(rose.euros(21800), Decimal("33.23"))
        normal = rose.bill(3, [("Travaux de nouage", 900), ("Maintien des positions", 1200)], "retour")
        self.assertEqual(normal.total, 21150)
        self.assertEqual(rose.euros(normal.total), Decimal("32.24"))
        fined = rose.bill(3, [("Travaux de nouage", 900), ("Maintien des positions", 1200)], "moderation")
        self.assertEqual(fined.total - normal.total, 50000)
        self.assertEqual(len(normal.rows) + len(normal.final), 59)
        self.assertIn("3 messages", "\n".join(normal.rows))
        self.assertNotIn("utilisé", "\n".join(normal.rows))
        for row in (*fined.rows, *fined.final):
            self.assertLessEqual(len(chat.safe_display(row)), 39)
        # Independently reconcile every printed item with the integer total.
        import re
        amounts = re.findall(r"(\d+),(\d{2}) F$", "\n".join(normal.rows), re.MULTILINE)
        self.assertEqual(sum(int(f)*100 + int(c) for f, c in amounts), normal.total)


class ProviderTests(unittest.TestCase):
    def test_only_documented_violation_triggers_fine(self):
        self.assertTrue(rose.moderation_rejected(403, violation()))
        service_error = {"code": 3201, "guardrails": [{"moderation_llm_v2": {"action": "block", "error": {}}}]}
        cases = [(403, {}), (403, {"message": "content blocked"}), (403, service_error),
                 (429, violation()), (401, violation()), (500, violation()), (200, violation())]
        false_decision = violation()
        false_decision["guardrails"]["results"]["moderation_llm_v2"]["decisions"]["sexual"]["violated"] = False
        cases.append((403, false_decision))
        inconsistent_error = violation()
        inconsistent_error["guardrails"]["results"]["moderation_llm_v2"]["error"] = {}
        cases.append((403, inconsistent_error))
        for status, data in cases:
            self.assertFalse(rose.moderation_rejected(status, data))

    def test_request_uses_configured_key_and_model_no_tools_or_retry(self):
        response = Mock(status_code=200)
        response.json.return_value = {"choices": [{"finish_reason": "stop", "message": {"content": "Je préfère rester dans les sous-entendus."}}]}
        with patch.object(rose.requests, "post", return_value=response) as post:
            result = rose.MistralModel("fixture-key", "configured-model")("persona", [{"role": "user", "content": "bonjour"}])
        self.assertEqual(result.status, "done")  # refusal prose remains conversational
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["model"], "configured-model")
        self.assertEqual(payload["max_tokens"], 180)
        for field in ("tools", "guardrails", "safe_prompt", "conversation_id"):
            self.assertNotIn(field, payload)
        post.assert_called_once()

    def test_api_failures_are_recoverable_without_diagnostics_or_retries(self):
        for status, data, expected in [
            (403, violation(), "moderation"), (403, {"message": "PRIVATE"}, "error"),
            (200, {"choices": []}, "error"),
            (200, {"choices": [{"finish_reason": "length", "message": {"content": "truncated"}}]}, "error")
        ]:
            with patch.object(rose.requests, "post", return_value=Mock(status_code=status, json=lambda data=data: data)) as post:
                result = rose.MistralModel("key", "model")("prompt", [])
                self.assertEqual(result, rose.Reply(status=expected))
                post.assert_called_once()
        with patch.object(rose.requests, "post", side_effect=rose.requests.Timeout("SECRET")) as post:
            self.assertEqual(rose.MistralModel("key", "model")("prompt", []).status, "error")
            post.assert_called_once()
        with patch.object(rose.requests, "post") as post:
            self.assertEqual(rose.MistralModel("", "model")("prompt", []).status, "configuration")
            post.assert_not_called()


class CancellationTests(unittest.TestCase):
    def test_blocked_worker_exit_discards_late_reply_and_all_session_state(self):
        release = threading.Event()
        entered = threading.Event()

        def model(prompt, history):
            entered.set()
            release.wait(2)
            return rose.Reply("STALE PRIVATE RESPONSE")

        for keys, expected in [([("fn", chat.K_RETOUR)], "retour"),
                               (typed(" BISCOTTE "), "safeword"),
                               ([('fn', chat.K_SOMMAIRE)], "sommaire"),
                               (typed("Je veux arreter"), "retour")]:
            release.clear()
            entered.clear()
            session = rose.Session("Jacqueline")
            job = session.start(model, "fictional private input")
            try:
                self.assertTrue(entered.wait(1))
                self.assertEqual(terminal.wait(KeyTerm(keys), job, "biscotte", 30), (None, expected))
                receipt = session.close(expected)
                self.assertEqual(receipt.messages, 1)
            finally:
                release.set()
                job.thread.join(1)
            self.assertTrue(job.output.empty())
            self.assertEqual(session.history, [])

    def test_deadline_does_not_accept_finished_or_late_response(self):
        job = rose.Job(rose.MockModel(), "prompt", [])
        job.thread.join(1)
        with patch.object(terminal.time, "monotonic", return_value=job.started + 40):
            self.assertEqual(terminal.wait(KeyTerm([]), job, "navet", 30), (None, "deadline"))
        self.assertTrue(job.output.empty())

    def test_queued_response_loses_to_exit(self):
        job = rose.Job(rose.MockModel(), "prompt", [])
        job.thread.join(1)
        self.assertEqual(terminal.wait(KeyTerm([("fn", chat.K_RETOUR)]), job, "navet", 30), (None, "retour"))
        self.assertTrue(job.output.empty())

    def test_bill_skip_cancel_page_mode_and_no_stale_rows(self):
        receipt = rose.bill(2, [], "retour")
        for key, expected in [(chat.K_SUITE, "done"), (chat.K_RETOUR, "retour"), (chat.K_SOMMAIRE, "sommaire")]:
            term = KeyTerm([("fn", key)])
            action = terminal.show_bill(term, receipt)
            self.assertEqual(action, expected)
            self.assertEqual(term.rows[-1], chat.SCROLL_OFF)
            self.assertNotIn("Connexion discrète", str(term.rows))
            if key == chat.K_SUITE:
                self.assertIn("Total", str(term.rows))
                self.assertIn("facture telephonique", str(term.rows))

    def test_complete_receipt_auto_scroll_final_block_visible(self):
        uart = startup.FakeSerial(b"")
        term = chat.Term(uart)
        with patch.object(chat.time, "sleep"):
            terminal.show_bill(term, rose.bill(3, [], "retour"), row_delay=0)
        screen, scrolling = startup.render_videotex(uart.output)
        self.assertFalse(scrolling)
        self.assertIn("Total", "\n".join(screen))
        self.assertIn("facture telephonique.", "\n".join(screen))
        self.assertTrue(screen[0].startswith("Total"))

    def test_outstanding_worker_is_returned_for_main_loop_overlap_guard(self):
        release = threading.Event()
        entered = threading.Event()

        def model(prompt, history):
            entered.set()
            release.wait(2)
            return rose.Reply("STALE")

        adapter = Mock(ready=lambda: True, side_effect=model)
        term = KeyTerm(typed("fixture") + [("fn", chat.K_RETOUR)])
        pending = None
        try:
            with patch.dict(os.environ, {"ROSE_PASSWORD": "fixture"}), patch.object(terminal, "show_bill", return_value="done"):
                action, pending = terminal.run(term, "Mireille", adapter)
            self.assertEqual(action, "retour")
            self.assertTrue(entered.is_set())
            self.assertTrue(pending[0].is_alive())
            adapter.assert_called_once()
        finally:
            release.set()
            if pending:
                pending[0].join(1)
        self.assertTrue(pending[1].empty())


class IntegrationTests(unittest.TestCase):
    def test_missing_key_after_gate_does_not_generate_or_bill(self):
        with patch.dict(os.environ, {"ROSE_PASSWORD": "fixture"}), patch.object(terminal, "show_bill") as billing, patch.object(rose.requests, "post") as post:
            terminal.run(KeyTerm(typed("fixture")), "Jacqueline", rose.MistralModel("", "model"))
        post.assert_not_called()
        billing.assert_not_called()

    def test_rejected_submission_counts_once_no_extra_persona_call(self):
        replies = iter([rose.Reply("Bonsoir."), rose.Reply(status="moderation")])
        model = Mock(ready=lambda: True, side_effect=lambda prompt, history: next(replies))
        term = KeyTerm(typed("fixture") + typed("fictional rejected submission"))
        with patch.dict(os.environ, {"ROSE_PASSWORD": "fixture"}), patch.object(terminal, "wait", side_effect=completed), patch.object(terminal, "show_bill", return_value="done") as billing:
            terminal.run(term, "Jacqueline", model)
        self.assertEqual(model.call_count, 2)
        receipt = billing.call_args.args[1]
        self.assertEqual(receipt.messages, 1)
        self.assertEqual(receipt.total, 70450)
        self.assertNotIn("rejected submission", str(receipt))

    def test_footer_is_french_through_gate_then_restored(self):
        term = KeyTerm(typed("wrong"))
        term.s.hint = "English keyboard"
        observed = []
        original = terminal.display

        def display(term, text):
            observed.append(term.s.hint)
            original(term, text)

        with patch.dict(os.environ, {"ROSE_PASSWORD": "fixture"}), patch.object(terminal, "display", side_effect=display):
            terminal.run(term, "Jacqueline", rose.MockModel())
        self.assertTrue(all("Entree ENVOI" in hint for hint in observed))
        self.assertEqual(term.s.hint, "English keyboard")

    def test_recoverable_failure_waits_for_next_input_without_fine(self):
        replies = iter([rose.Reply("Bonsoir."), rose.Reply(status="error"), rose.Reply("Reprenons.")])
        model = Mock(ready=lambda: True, side_effect=lambda prompt, history: next(replies))
        term = KeyTerm(typed("fixture") + typed("premier essai") + typed("second essai") + typed("RETOUR"))
        with patch.dict(os.environ, {"ROSE_PASSWORD": "fixture"}), patch.object(terminal, "wait", side_effect=completed), patch.object(terminal, "show_bill", return_value="done") as billing:
            terminal.run(term, "Jacqueline", model)
        self.assertEqual(model.call_count, 3)
        receipt = billing.call_args.args[1]
        self.assertEqual(receipt.messages, 2)
        self.assertNotIn("Amende", str(receipt))
        self.assertIn("Reprenons", str(term.rows))

    def test_username_prompt_after_sommaire_preserves_bill_on_screen(self):
        uart = startup.FakeSerial(b"owner" + startup.fn(chat.K_ENVOI))
        with patch.object(chat.memory_session, "MemorySession") as memory:
            chat.select_memory_user(chat.Term(uart), preserve_display=True)
        self.assertNotIn(chat.FF, uart.output)
        memory.assert_called_once_with("owner")

    def test_full_gate_conversation_help_exit_bill_rejection_and_roster(self):
        real_bill = terminal.show_bill
        for alias, keys in [("Jacqueline", typed("fixture") + typed("Bonjour") + typed("AIDE") + typed("RETOUR")),
                            ("", typed("fixture") + [("fn", chat.K_SUITE)] + typed("Mireille") + typed("SOMMAIRE"))]:
            term = KeyTerm(keys)
            receipts = []

            def show(term, receipt, delay, receipts=receipts):
                receipts.append(receipt)
                return real_bill(term, receipt, 0)

            with patch.dict(os.environ, {"ROSE_PASSWORD": "fixture"}), patch.object(terminal, "wait", side_effect=completed), patch.object(terminal, "show_bill", side_effect=show):
                terminal.run(term, alias, rose.MockModel())
            self.assertEqual(len(receipts), 1)
            self.assertEqual(receipts[0].messages, int(bool(alias)))
            self.assertIn("Mot d'arret", str(term.rows))
            self.assertIn("France Telecom", str(term.rows))
            self.assertNotIn("fixture", str(term.rows))
        # Structured rejection goes straight to bill, with no persona response.
        model = Mock(ready=lambda: True, return_value=rose.Reply(status="moderation"))
        term = KeyTerm(typed("fixture"))
        with patch.dict(os.environ, {"ROSE_PASSWORD": "fixture"}), patch.object(terminal, "wait", side_effect=completed), patch.object(terminal, "show_bill", return_value="done") as billing:
            terminal.run(term, "Armand", model)
            self.assertIn("Amende", str(billing.call_args.args[1].rows))
            self.assertEqual(model.call_count, 1)

    def test_main_english_restored_and_no_rose_turns_reach_memory_or_printer(self):
        keys = (b"3615 JACQUELINE" + startup.fn(chat.K_ENVOI)
                + b"fixture" + startup.fn(chat.K_ENVOI)
                + b"PRIVATE ROSE TURN" + startup.fn(chat.K_ENVOI)
                + startup.fn(chat.K_RETOUR)
                + b"Public question" + startup.fn(chat.K_ENVOI))
        uart = startup.FakeSerial(keys)
        memory = Mock(key="owner", username="owner", pending=None, take_warning=lambda: "")
        memory.saved_language.return_value = "en"
        requests = []

        def start(prompt, history, language, *args, **kwargs):
            requests.append((history, language))
            output = chat.Queue()
            output.put(({"display": "Public answer", "turn": {"role": "assistant", "content": "Public answer"}}, None))
            return Mock(is_alive=lambda: False), output, chat.time.monotonic()

        with (patch.dict(os.environ, {"ROSE_PASSWORD": "fixture"}),
              patch.object(chat, "select_memory_user", return_value=(memory, "envoi")),
              patch.object(chat, "show_boot", return_value="done"), patch.object(chat, "prepare_boot", return_value=[]),
              patch.object(chat, "load_preset", return_value=("persona", "3615 IA", "Question?", "WAIT", None)),
              patch.object(chat, "build_title", return_value=["LOGO"]),
              patch.object(chat, "start_answer", side_effect=start),
              patch.object(chat.gmail_mail, "enabled", return_value=False),
              patch.object(rose, "MistralModel", return_value=rose.MockModel()),
              patch.object(terminal, "wait", side_effect=completed),
              patch.object(terminal, "show_bill", return_value="done"),
              patch.object(chat.time, "sleep"),
              self.assertRaises(startup.EndOfInput)):
            chat.run(chat.Term(uart))
        self.assertEqual(requests, [([{"role": "user", "content": "Public question"}], "en")])
        self.assertNotIn("PRIVATE", str(memory.mock_calls))
        self.assertNotIn("fixture", str(memory.mock_calls))
        memory.remember_language.assert_not_called()
        output = startup.screen_text(uart.output)
        self.assertIn("Mot de passe", output)
        self.assertIn("Mot d'arret", output)

    def test_pending_composition_blocks_entry_interception(self):
        for mail in [Mock(draft={"body": "draft"}, composition=None, recipient_choices=[]),
                     Mock(draft=None, composition={"instruction": "pending"}, recipient_choices=[])]:
            self.assertTrue(chat.calculator_blocked(mail, None))
        self.assertIsNone(rose.entry("A: 3615 Jacqueline"))

    def test_console_suppresses_password_local_echo(self):
        import curses
        window = Mock()
        window.get_wch.return_value = "x"
        window.getmaxyx.return_value = (26, 80)
        serial = ConsoleSerial(window)
        serial.set_local_echo(False)
        self.assertEqual(serial.read(1), b"x")
        self.assertNotIn("x", "".join("".join(r) for r in serial.screen.rows))
        serial.set_local_echo(True)
        self.assertEqual(serial.read(1), b"x")
        self.assertIn("x", "".join(serial.screen.rows[0]))
        with patch.object(curses, "flushinp") as flush:
            serial.reset_input_buffer()
            flush.assert_called_once()

    def test_password_setup_private_and_not_in_git(self):
        from scripts.rose_console import set_password
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "password"
            with patch("getpass.getpass", side_effect=[" Fixture ", " Fixture "]):
                self.assertEqual(set_password(path), 0)
            self.assertEqual(path.read_text(), " Fixture ")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
