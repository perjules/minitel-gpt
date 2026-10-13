"""Local arithmetic, one-call fiction, context routing and interruptible displays."""

import json
import shutil
import subprocess
import threading
import unittest
from fractions import Fraction
from pathlib import Path
from queue import Queue
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from test_gmail import make_session
from test_terminal_startup import EndOfInput, FakeSerial, fn, screen_text

from services import admin_ui as admin
from services import calculator as calc
from services import memory_session as memory
from services import minitel_chatgpt as terminal


class ArithmeticTests(unittest.TestCase):
    def test_precedence_decimals_unary_and_parentheses(self):
        for expression, result in (
            ("17*23", 391),
            ("2+3*4", 14),
            ("(2+3)*4", 20),
            ("-2*-3", 6),
            ("--2", 2),
            ("3/-2", Fraction(-3, 2)),
            ("0,1+0.2", Fraction(3, 10)),
            (".5+5.", Fraction(11, 2)),
            ("+2-(3-(4/2))", 1),
        ):
            with self.subTest(expression=expression):
                self.assertEqual(calc.evaluate(expression), result)

    def test_zero_denominators_are_detected_in_actual_evaluation(self):
        for expression in ("17/0", "0/0", "17/(2-2)", "1/(-0.0)", "1/(3/2-1,5)"):
            with (
                self.subTest(expression=expression),
                self.assertRaises(ZeroDivisionError),
            ):
                calc.evaluate(expression)

    def test_restricted_tree_and_malformed_inputs(self):
        for expression in (
            "__import__('os').system('echo hi')",
            "1**2",
            "1//2",
            "1%2",
            "[1]",
            "abs(-1)",
            "1e9",
            "0xFF",
            "True",
            "1+",
            "()",
            "1..2",
            "1,2,3",
            "2\n+2",
            "1/0+bad",
            "(1/0)**2",
        ):
            with (
                self.subTest(expression=expression),
                self.assertRaises(calc.ArithmeticProblem),
            ):
                calc.evaluate(expression)

    def test_length_complexity_and_intermediate_numerical_limits(self):
        for expression in (
            "1" * 257,
            "1" * 81,
            "(" * 17 + "1" + ")" * 17,
            "+".join(["1"] * 60),
            "*".join(["9" * 75] * 5),
        ):
            with (
                self.subTest(expression=expression),
                self.assertRaises(calc.ArithmeticProblem),
            ):
                calc.evaluate(expression)

    def test_intermediate_bit_budget(self):
        with (
            patch.object(calc, "MAX_BITS", 32),
            self.assertRaises(calc.ArithmeticProblem),
        ):
            calc.evaluate("999999999*999999999")

    def test_six_significant_digits_scientific_notation_and_rounding(self):
        for value, expected in (
            (Fraction(1, 3), "~0.333333"),
            (Fraction(7, 3), "~2.33333"),
            (Fraction(123456789), "~1.23457e8"),
            (Fraction(1, 10**7), "1e-7"),
            (Fraction(1, 10000), "0.0001"),
            (Fraction(1200), "1200"),
            (Fraction(1, 8), "0.125"),
            (Fraction(-1, 3), "~-0.333333"),
            (Fraction(0), "0"),
            (Fraction(9999996, 10), "~1e6"),
        ):
            with self.subTest(value=value):
                self.assertEqual(calc.format_result(value), expected)

    def test_conservative_detection_and_explicit_console_share_results(self):
        state = calc.Session()
        for question in ("2", "0,2", "CALCULUS", "What is 2+2?", "CALL Alice"):
            self.assertIsNone(state.handle(question))
        first = state.handle("2+2")["factual"]
        state.handle("CALC")
        self.assertTrue(state.mode)
        self.assertEqual(state.handle("2+2")["factual"], first)
        self.assertEqual(state.handle("2")["factual"], "2 = 2")
        self.assertIsNone(state.handle("bad input")["turn"])
        state.handle("RETOUR")
        self.assertFalse(state.mode)
        self.assertEqual(state.handle("CALC 2+2")["factual"], first)
        self.assertFalse(state.mode)

    def test_ordinary_and_invalid_calculations_never_call_generator(self):
        generator = Mock(return_value=calc.content("fr")["zero_scene"])
        for question in ("2+2", "0,2+0.3", "1/", "(2)"):
            calc.Session().handle(question, generate=generator)
        generator.assert_not_called()
        calc.Session().handle("17/(2-2)", generate=generator)
        generator.assert_called_once_with("17/(2-2)", "fr")

    def test_authored_templates_reference_and_display_bounds(self):
        for language in ("fr", "en"):
            content = calc.content(language)
            self.assertEqual(len(content["ordinary"]), 32)
            self.assertEqual(len(set(content["ordinary"])), 32)
            for phrase in content["ordinary"]:
                self.assertFalse(phrase.isupper())
                self.assertLess(len(phrase), 120)
            calc.validate_scene(json.dumps(content["zero_scene"]))
            result = calc.Session().handle("0/0", language)
            self.assertIn(content["undefined"], result["scene"])
            rows = list(
                calc.frames([terminal.safe_display(s) for s in result["scene"]])
            )
            self.assertTrue(all(len(row) <= 39 for row in rows))
            self.assertLess(len(rows), 170)


class GenerationTests(unittest.TestCase):
    def response(self, scene, finish="stop"):
        response = Mock()
        response.json.return_value = {
            "choices": [{"finish_reason": finish, "message": {"content": scene}}]
        }
        return response

    def test_one_simple_request_contains_only_expression_language_reference(self):
        scene = calc.content("fr")["zero_scene"]
        with patch.object(
            calc.requests, "post", return_value=self.response(json.dumps(scene))
        ) as post:
            result = calc.generate_scene("17/(2-2)", "fr", "fake-key", "model")
        self.assertEqual(result, scene)
        post.assert_called_once()
        kwargs = post.call_args.kwargs
        self.assertEqual(kwargs["timeout"], 12)
        self.assertNotIn("tools", kwargs["json"])
        request = json.loads(kwargs["json"]["messages"][1]["content"])
        self.assertEqual(set(request), {"expression", "language", "reference"})
        self.assertEqual(request["expression"], "17/(2-2)")
        self.assertEqual(kwargs["json"]["response_format"], {"type": "json_object"})

    def test_missing_key_and_generation_failures_use_reference_without_retry(self):
        with patch.object(calc.requests, "post") as post:
            self.assertEqual(
                calc.generate_scene("17/0", "fr", "", "model"),
                calc.content("fr")["zero_scene"],
            )
            post.assert_not_called()
        for response in (
            self.response("bad JSON"),
            self.response("{}"),
            self.response("{}", finish="length"),
        ):
            with patch.object(calc.requests, "post", return_value=response) as post:
                self.assertEqual(
                    calc.generate_scene("17/0", "fr", "fake", "model"),
                    calc.content("fr")["zero_scene"],
                )
                post.assert_called_once()
        with patch.object(
            calc.requests, "post", side_effect=calc.requests.Timeout
        ) as post:
            self.assertEqual(
                calc.generate_scene("0/0", "en", "fake", "model"),
                calc.content("en")["zero_scene"],
            )
            post.assert_called_once()

    def test_scene_validation_rejects_controls_overflow_generic_errors_and_caps(self):
        reference = calc.content("fr")["zero_scene"]
        variants = []
        for text in (
            "Erreur\x1bcritique",
            "ERREUR CRITIQUE DU CALCULATEUR",
            "https://fake.example",
            "x" * 451,
        ):
            variant = json.loads(json.dumps(reference))
            variant["escalation"][0] = text
            variants.append(variant)
        variants.append({**reference, "unexpected": "payload"})
        variants.append({**reference, "maintenance": ["Aucun problème"] * 4})
        for variant in variants:
            with self.assertRaises(ValueError):
                calc.validate_scene(json.dumps(variant))


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.selected = memory.MemorySession("Test", root=self.temp.name, model=Mock())
        self.addCleanup(self.selected.close)
        self.state = calc.Session()

    def answer(self, question, mail=None):
        return terminal.call_answer(
            "persona",
            [{"role": "user", "content": question}],
            "fr",
            ("conversations", True, 60),
            "",
            mail=mail,
            memory=self.selected,
            calc=self.state,
        )

    def test_calculation_uses_no_public_llm_and_keeps_only_fact(self):
        with (
            patch.object(terminal.mistral_answers, "answer") as llm,
            patch.object(calc, "generate_scene") as generator,
        ):
            response = self.answer("17*23")
        llm.assert_not_called()
        generator.assert_not_called()
        self.assertEqual(response["turn"]["content"], "17*23 = 391")
        self.selected.record(
            "assistant", response["display"], "theatre", factual=response["factual"]
        )
        self.assertEqual(self.selected.last_answer, "17*23 = 391")
        self.assertNotIn("coprocesseur", str(self.selected.recent))

    def test_numeric_choices_and_draft_revisions_keep_existing_routes(self):
        mail, _api, _model = make_session()
        with (
            patch.dict("os.environ", {"GMAIL_ENABLED": "1"}),
            patch.object(calc, "generate_scene") as generator,
        ):
            mail.handle("MAIL")
            result = self.answer("1", mail)
            self.assertNotIn("calculator", result)
            mail.handle("REPONDRE remercier")
            draft = mail.draft
            result = self.answer("2+2", mail)
            self.assertNotIn("calculator", result)
            self.answer("CALC", mail)
            self.assertEqual(self.answer("2+2", mail)["factual"], "2+2 = 4")
            self.answer("RETOUR", mail)
            self.assertFalse(self.state.mode)
            self.assertIsNotNone(mail.draft)
            self.assertEqual(mail.draft["recipient"], draft["recipient"])
        generator.assert_not_called()

    def test_pending_memory_choice_retained_during_explicit_console(self):
        pending = {"choices": [], "snapshot": {"items": []}}
        self.selected.pending = pending
        self.selected.model.plan.return_value = {
            "operation": "clarify",
            "clarification": "Quel élément ?",
        }
        result = self.answer("2+2")
        self.assertNotIn("calculator", result)
        self.selected.pending = pending
        self.answer("CALC")
        self.assertEqual(self.answer("2+2")["factual"], "2+2 = 4")
        self.answer("RETOUR")
        self.assertIs(self.selected.pending, pending)

    def test_pending_context_still_blocks_auto_when_handler_passes_through(self):
        self.selected.pending = {"choices": [], "snapshot": {"items": []}}

        def public(*args):
            self.selected.pending = None
            return {"passthrough": True}

        response = {
            "status": "ordinary",
            "text": "ordinary",
            "chunks": [],
            "references": [],
        }
        with (
            patch.object(self.selected, "handle", side_effect=public),
            patch.object(
                terminal.mistral_answers, "answer", return_value=response
            ) as llm,
        ):
            result = self.answer("2+2")
        self.assertNotIn("calculator", result)
        llm.assert_called_once()

    def test_zero_scene_logged_but_never_usable_as_factual_memory(self):
        result = calc.Session().handle("17/0")
        self.selected.record(
            "assistant", result["display"], "theatre", factual=result["factual"]
        )
        self.assertIn("indéfini", self.selected.last_answer)
        self.assertNotIn("K7", self.selected.last_answer)
        self.assertNotIn("Alcatel", str(self.selected.recent))
        path = next((Path(self.temp.name) / self.selected.key / "logs").glob("*.jsonl"))
        records = [json.loads(row) for row in path.read_text().splitlines()]
        self.assertEqual(records[-1]["channel"], "theatre")
        self.assertIn("K7", records[-1]["text"])
        self.selected.model.plan.return_value = {
            "operation": "save",
            "kind": "note",
            "title": "calcul",
            "source": "last_answer",
        }
        saved = self.selected.handle("Garde cette réponse sous le nom calcul.")
        self.assertNotIn("K7", saved["display"])
        self.assertIn("indéfini", saved["display"])

    def test_reset_abandons_model_worker_and_no_late_uart_frames(self):
        entered, release = threading.Event(), threading.Event()

        def generate(*args):
            entered.set()
            release.wait(2)
            return calc.content("fr")["zero_scene"]

        with patch.object(calc, "generate_scene", side_effect=generate):
            pending = terminal.start_answer(
                "",
                [{"role": "user", "content": "17/0"}],
                "fr",
                ("conversations", True, 60),
                "",
                calc=self.state,
            )
            self.assertTrue(entered.wait(1))
            term = object.__new__(terminal.Term)
            term.s = FakeSerial(fn(terminal.K_SOMMAIRE))
            self.assertEqual(
                terminal.wait_answer(term, pending, 60, allow_retour=True),
                (None, "sommaire"),
            )
            release.set()
            pending[0].join(1)
            self.assertEqual(term.s.output, b"")
            self.assertIn("scene", pending[1].get_nowait()[0])


class PlaybackTests(unittest.TestCase):
    def term(self, interrupt=None, after=1, slow=False):
        clock = [0.0]
        t = Mock()
        t.rows = []
        t.line.side_effect = t.rows.append
        calls = [0]

        def read(timeout):
            calls[0] += 1
            clock[0] += 95 if slow else max(timeout, 0.001)
            if interrupt is not None and calls[0] == after:
                return "fn", interrupt
            return "timeout", None

        t.read_key.side_effect = read
        return t, clock

    def test_progressive_narrow_scene_and_restored_page_mode(self):
        t, clock = self.term()
        with patch.object(
            terminal.time, "monotonic", side_effect=lambda clock=clock: clock[0]
        ):
            result = terminal.show_calculator_scene(
                t, calc.Session().handle("17/0")["scene"], "fr"
            )
        self.assertEqual(result, "done")
        self.assertTrue(all(len(row) <= 39 for row in t.rows))
        self.assertTrue(any("indefini" in row for row in t.rows))
        self.assertTrue(any("Alcatel" in row for row in t.rows))
        self.assertEqual(t.w.call_args.args[0], terminal.SCROLL_OFF)
        self.assertLess(clock[0], 35)

    def test_sommaire_guide_retour_stop_without_late_lines(self):
        for key, action in (
            (terminal.K_SOMMAIRE, "sommaire"),
            (terminal.K_GUIDE, "guide"),
            (terminal.K_RETOUR, "retour"),
        ):
            t, clock = self.term(key, after=8)
            with patch.object(
                terminal.time, "monotonic", side_effect=lambda clock=clock: clock[0]
            ):
                result = terminal.show_calculator_scene(
                    t, ["Première étape", "NEVER DISPLAY THIS"], "fr"
                )
            self.assertEqual(result, action)
            self.assertNotIn("NEVER DISPLAY THIS", t.rows)
            t.s.reset_output_buffer.assert_called_once()
            self.assertEqual(t.w.call_args.args[0], terminal.SCROLL_OFF)

    def test_playback_has_hard_deadline(self):
        t, clock = self.term(slow=True)
        with patch.object(
            terminal.time, "monotonic", side_effect=lambda clock=clock: clock[0]
        ):
            self.assertEqual(
                terminal.show_calculator_scene(t, ["Never sent"], "fr"), "retour"
            )
        self.assertNotIn("Never sent", t.rows)

    def test_retour_key_exits_only_calculator_input(self):
        for mode, keys, expected in (
            (True, b"12" + fn(terminal.K_RETOUR), "RETOUR"),
            (False, b"12" + fn(terminal.K_RETOUR) + fn(terminal.K_ENVOI), "1"),
        ):
            term = object.__new__(terminal.Term)
            term.s = FakeSerial(keys)
            self.assertEqual(
                terminal.read_question(term, calculator_mode=mode), (expected, "envoi")
            )


class AdminAndUARTTests(unittest.TestCase):
    def test_admin_javascript_cancels_late_responses_and_frames(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Optional Node runtime unavailable for browser-code test")
        source = admin.ADMIN_HTML
        start = source.index("let testSequence=0;")
        end = source.index("\nloadPreset();", start)
        result = subprocess.run(
            [node, str(Path(__file__).with_name("calculator_preview.js"))],
            input=json.dumps({"script": source[start:end]}),
            capture_output=True,
            check=False,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        environment = patch.dict(
            "os.environ", {"MINITEL_MEMORY_DIR": self.temp.name, "GMAIL_ENABLED": "0"}
        )
        environment.start()
        self.addCleanup(environment.stop)
        self.addCleanup(self.clear_admin)
        self.clear_admin()
        self.client = admin.app.test_client()
        with self.client.session_transaction() as cookie:
            cookie["logged_in"] = True

    def clear_admin(self):
        for identifier in list(admin.MEMORY_SESSIONS):
            admin.discard_memory(identifier)

    def post(self, message, **extra):
        return self.client.post(
            "/test-preset",
            json={
                "preset_key": "p",
                "memory_user": "Owner",
                "message": message,
                **extra,
            },
        )

    def test_admin_local_console_zero_generation_and_reset(self):
        with (
            patch.object(
                admin,
                "load_prompts",
                return_value={"presets": {"p": {"system": "persona"}}},
            ),
            patch.object(admin, "load_knowledge_blob", return_value=""),
            patch.object(admin, "llm_answer", return_value="Ordinary response") as llm,
            patch.object(
                calc, "generate_scene", return_value=calc.content("fr")["zero_scene"]
            ) as generate,
        ):
            self.assertIn("= 4", self.post("2+2").json["answer"])
            self.assertTrue(self.post("CALC").json["calculator_mode"])
            self.assertIn("= 2", self.post("2").json["answer"])
            result = self.post("17/0").json
            self.assertTrue(result["scene"])
            self.assertTrue(all(len(row) <= 39 for row in result["scene"]))
            generate.assert_called_once()
            llm.assert_not_called()
            self.assertFalse(self.post("RETOUR").json["calculator_mode"])
            self.assertIn("Ordinary response", self.post("Hello").json["answer"])
            self.post("CALC")
            self.post("", reset=True)
            self.post("2")
            self.assertEqual(llm.call_count, 2)

    def test_real_uart_console_arithmetic_exit_and_conversation_recovery(self):
        uart = FakeSerial(
            b"Owner"
            + fn(terminal.K_ENVOI)
            + b"fr"
            + fn(terminal.K_ENVOI)
            + fn(terminal.K_SUITE)
            + b"CALC"
            + fn(terminal.K_ENVOI)
            + b"0.1+0,2"
            + fn(terminal.K_ENVOI)
            + b"2"
            + fn(terminal.K_ENVOI)
            + fn(terminal.K_RETOUR)
            + b"Bonjour"
            + fn(terminal.K_ENVOI)
            + fn(terminal.K_SOMMAIRE)
        )

        def synchronous(*args, **kwargs):
            queue = Queue()
            queue.put((terminal.call_answer(*args, **kwargs), None))
            return Mock(is_alive=lambda: False), queue, terminal.time.monotonic()

        result = {
            "status": "ordinary",
            "text": "Bonjour",
            "chunks": [],
            "references": [],
        }
        with (
            patch.object(terminal.serial, "Serial", return_value=uart),
            patch.object(terminal.time, "sleep"),
            patch.object(
                terminal,
                "load_preset",
                return_value=("persona", "3615 IA", "Question?", "WAIT", None),
            ),
            patch.object(terminal, "build_title", return_value=["LOGO"]),
            patch.object(terminal, "factual_knowledge", return_value=""),
            patch.object(
                terminal, "prepare_boot", return_value=terminal.UI["fr"]["boot"]
            ),
            patch.object(terminal, "start_answer", side_effect=synchronous),
            patch.object(
                terminal.mistral_answers, "answer", return_value=result
            ) as llm,
            self.assertRaises(EndOfInput),
        ):
            terminal.run()
        visible = screen_text(uart.output)
        self.assertIn("= 0.3", visible)
        self.assertIn("Conversation\r\nreprise", visible)
        llm.assert_called_once()
        self.assertIn("0.1+0,2 = 0.3", str(llm.call_args.args[0]))
        self.assertNotIn("Coprocesseur", str(llm.call_args.args[0]))
