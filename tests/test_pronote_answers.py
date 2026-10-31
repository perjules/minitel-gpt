"""Conversations function-call contracts and private school / public web isolation."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from test_terminal_startup import FakeSerial, fn, screen_text

from services import admin_ui as admin
from services import memory_session
from services import minitel_chatgpt as chat
from services import mistral_answers as engine
from services import pronote as pn
from services import pronote_answers as pa


def final(text="Maths : exercice 2 pour demain."):
    return {"outputs": [{"type": "message.output", "role": "assistant", "content": text}]}


def call(name="pronote_get_homework", identifier="call-1", arguments=None):
    return {"type": "function.call", "name": name, "tool_call_id": identifier,
            "arguments": arguments or {"child": "Alex", "from": "2026-10-12", "to": "2026-10-12"}}


class SchoolMemoryRoutingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.memory = memory_session.MemorySession("test", root=self.temp.name, model=Mock())
        self.addCleanup(self.memory.close)
        self.backend = Mock()
        self.backend.tools.return_value = [{"type": "function"}]
        self.backend.config.return_value = {"enabled":True,"children": {"Alex": {}, "Élodie": {}}}
        registry=self.memory.registry
        u=self.memory.user
        registry.configure(u.id,u.display_name,True,['test'],None,False,['Alex','Élodie'])
        self.memory.user=registry.get(u.id)
        self.backend.tools.return_value=[{'function':{'parameters':{'properties':{'child':{'enum':['Alex','Élodie']}}}}}]

    def ask(self, question):
        result = {"status": "ordinary", "text": "Données scolaires", "chunks": [], "references": []}
        with (
            patch.object(chat, "PROVIDER", "mistral"),
            patch.object(chat.pronote, "backend", return_value=self.backend),
            patch.object(self.memory, "handle", return_value={"display": "mémoire", "turn": None}) as saved,
            patch.object(self.memory, "context", return_value="PRIVATE SAVED NOTES") as context,
            patch.object(chat.mistral_answers, "answer", return_value=result) as answer,
        ):
            response = chat.call_answer("persona", [{"role": "user", "content": question}],
                                        "fr", ("conversations", True, 60), "knowledge", memory=self.memory)
        return response, saved, context, answer

    def test_french_school_requests_bypass_saved_notes_and_pending_memory(self):
        for pending in (None, {"choices": ["some-saved-note"]}):
            self.memory.pending = pending
            for question in ("Montre moi les notes de Alex", 'Quels sont les devoirs de "Alex"',
                             "Montre-moi les notes d'Élodie", "Les notes de Elodie", "Devoirs demain ?"):
                with self.subTest(question=question, pending=bool(pending)):
                    _, saved, context, answer = self.ask(question)
                    saved.assert_not_called()
                    context.assert_not_called()
                    self.assertIs(answer.call_args.kwargs["pronote"].backend, self.backend)
                    self.assertEqual(answer.call_args.args[0][-1]["content"], question)
                    self.assertEqual(self.memory.pending, pending)

    def test_explicit_memory_and_general_saved_notes_keep_their_route(self):
        for question in ("Montre mes notes de lecture", "Retiens les notes de Alex",
                         "Note les devoirs de Alex", "Montre la liste des devoirs"):
            with self.subTest(question=question):
                _, saved, _, answer = self.ask(question)
                saved.assert_called_once()
                answer.assert_not_called()

    def test_bare_memory_choice_is_not_a_school_request(self):
        self.memory.pending = {"choices": ["some-saved-note"]}
        _, saved, _, answer = self.ask("1")
        saved.assert_called_once()
        answer.assert_not_called()

    def test_disabled_pronote_does_not_override_memory(self):
        self.backend.tools.return_value = []
        _, saved, _, answer = self.ask("Montre moi les notes de Alex")
        saved.assert_called_once()
        answer.assert_not_called()


class LocalAnswerTests(unittest.TestCase):
    def setUp(self):
        self.local = Mock()
        self.local.config.return_value = {"enabled": True, "children": {"Alex": {"name": "SECRET FULLNAME"}}}
        self.local.tools.return_value = [{"type": "function", "function": {"name": "pronote_get_homework"}}]
        self.local.execute.return_value = {"child": "Alex", "items": [{"description": "EXACT PRIVATE SCHOOL RECORD"}],
                                           "retrieved_at": "2026-10-11T20:00:00+02:00", "cached": True,
                                           "stale": True, "truncated": False}

    def request(self, responses, history=None):
        mocks = []
        for data in responses:
            response = Mock()
            response.json.return_value = data
            mocks.append(response)
        with patch.object(pa.requests, "post", side_effect=mocks) as post:
            result = engine.answer(history or [{"role": "user", "content": "Devoirs d'Alex demain ?"}],
                                   "fr", "secret-key", "model", True, 20, pronote=self.local)
        return result, post.call_args_list

    def test_private_calls_resume_with_minimal_results_store_false_no_web(self):
        result, calls = self.request([{"outputs": [{"type": "message.output", "content": "Working"}, call()]}, final()])
        self.assertTrue(result["private"])
        self.assertEqual(len(calls), 2)
        for request in calls:
            payload = request.kwargs["json"]
            self.assertFalse(payload["store"])
            self.assertFalse(payload["stream"])
            self.assertNotIn("web_search", json.dumps(payload["tools"]))
            self.assertNotIn("SECRET FULLNAME", json.dumps(payload))
            self.assertNotIn("conversation_id", payload)
            self.assertNotIn("MF-83", payload["instructions"])
        resumed = calls[1].kwargs["json"]["inputs"]
        self.assertEqual(resumed[-2], call())
        self.assertEqual(resumed[-1]["type"], "function.result")
        self.assertIn("EXACT PRIVATE SCHOOL RECORD", resumed[-1]["result"])
        self.assertNotIn("Working", json.dumps(resumed))
        self.assertLess(calls[1].kwargs["timeout"], calls[0].kwargs["timeout"])
        self.assertIn("possiblement perime", pa.footer(result, "fr"))
        self.assertIn("2026-10-11T20:00:00+02:00", pa.footer(result, "fr"))
        self.local.execute.assert_called_once()

    def test_public_search_excludes_private_question_answer_and_tools(self):
        history = [{"role": "user", "content": "PRIVATE CHILD QUESTION"},
                   {"role": "assistant", "content": "PRIVATE GRADES", "private": True},
                   {"role": "user", "content": "Search today's public news"}]
        result, requests = self.request([final("Public planning"), final("Public factual answer")], history)
        public = requests[-1].kwargs["json"]
        self.assertEqual(public["tools"], [{"type": "web_search"}])
        self.assertNotIn("PRIVATE", json.dumps(public))
        self.assertEqual(public["inputs"], [history[-1]])
        self.assertNotIn("MF-83", requests[0].kwargs["json"]["instructions"])
        self.assertTrue(public["instructions"].startswith(
            engine.CHAT_PROMPT_FILE.read_text(encoding="utf-8").strip()))
        self.local.execute.assert_not_called()
        self.assertNotIn("private", result)

    def test_public_followup_planner_can_echo_a_prior_verified_citation(self):
        citation = {"type": "tool_reference", "tool": "web_search", "title": "Price source",
                    "url": "https://example.org/price"}
        history = [{"role": "user", "content": "Price of a bicycle?"},
                   {"role": "assistant", "content": "2 000 EUR", "chunks": [
                       {"type": "text", "text": "2 000 EUR"}, citation],
                    "references": [{"title": citation["title"], "url": citation["url"]}]},
                   {"role": "user", "content": "What about the warranty?"}]
        planner = final([{"type": "text", "text": "Public follow-up "}, citation])
        result, calls = self.request([planner, final("Warranty details")], history)
        self.assertEqual(result["text"], "Warranty details")
        self.assertNotIn("private", result)
        self.assertEqual(len(calls), 2)
        self.assertNotIn("web_search", json.dumps(calls[0].kwargs["json"]["tools"]))
        self.assertEqual(calls[1].kwargs["json"]["tools"], [{"type": "web_search"}])
        self.local.execute.assert_not_called()

    def test_no_private_history_reaches_web_after_feature_is_disabled(self):
        history = [{"role": "user", "content": "PRIVATE QUESTION"},
                   {"role": "assistant", "content": "PRIVATE RECORD", "private": True},
                   {"role": "user", "content": "Public question"}]
        response = Mock()
        response.json.return_value = final("Public")
        with patch.object(engine.requests, "post", return_value=response) as post:
            engine.answer(history, "fr", "key", "model")
        self.assertNotIn("PRIVATE", json.dumps(post.call_args.kwargs["json"]))

    def test_no_local_call_cannot_be_presented_as_school_verification(self):
        result, requests = self.request([final("Invented grade 18/20")])
        self.assertNotIn("18/20", result["text"])
        self.assertIn("Aucune donnee PRONOTE", result["text"])
        self.assertTrue(result["private"])
        self.assertEqual(len(requests), 1)

    def test_bounds_repeated_ids_and_unexpected_hosted_tools(self):
        with self.assertRaisesRegex(ValueError, "call limit"):
            self.request([{"outputs": [call(identifier=str(i)) for i in range(5)]}])
        self.local.execute.assert_not_called()
        with self.assertRaisesRegex(ValueError, "Malformed local"):
            self.request([{"outputs": [call()]}, {"outputs": [call()]}])
        self.local.execute.reset_mock()
        with self.assertRaisesRegex(ValueError, "Unexpected private"):
            self.request([{"outputs": [call(), {"type": "tool.execution", "name": "web_search"}]}])
        self.local.execute.assert_not_called()
        self.local.execute.reset_mock()
        with self.assertRaisesRegex(ValueError, "call limit"):
            self.request([{"outputs": [call(identifier="1")]}, {"outputs": [call(identifier="2")]},
                          {"outputs": [call(identifier="3")]}])
        self.assertEqual(self.local.execute.call_count, 1)

    def test_two_rounds_finish_with_tools_disabled_instead_of_requesting_again(self):
        listed = call("pronote_list_children", identifier="list", arguments="{}")
        result, requests = self.request([{"outputs": [listed]}, {"outputs": [call()]}, final()])
        self.assertTrue(result["private"])
        self.assertEqual(self.local.execute.call_count, 2)
        self.assertEqual(requests[-1].kwargs["json"]["tools"], [])
        self.assertEqual(requests[-1].kwargs["json"]["completion_args"]["tool_choice"], "none")
        self.assertNotIn("web_search", json.dumps(requests[-1].kwargs["json"]["tools"]))

    def test_identical_calls_reuse_result_without_another_local_lookup(self):
        args = {"to": "2026-10-12", "child": "Alex", "from": "2026-10-12"}
        repeated = call(identifier="call-2", arguments=json.dumps(args))
        result, requests = self.request([{"outputs": [call()]}, {"outputs": [repeated]}, final()])
        self.local.execute.assert_called_once()
        self.assertEqual(requests[-1].kwargs["json"]["tools"], [])
        self.assertEqual(result["text"], "Maths : exercice 2 pour demain.")
        results = [entry["result"] for entry in requests[-1].kwargs["json"]["inputs"]
                   if entry.get("type") == "function.result"]
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0], results[1])

    def test_failed_tool_finishes_immediately_and_keeps_underlying_error_visible(self):
        self.local.execute.return_value = {"error": "child_unavailable"}
        result, requests = self.request([{"outputs": [call()]}, final("Les donnees ne sont pas disponibles.")])
        self.local.execute.assert_called_once()
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[-1].kwargs["json"]["tools"], [])
        self.assertEqual(requests[-1].kwargs["json"]["completion_args"]["tool_choice"], "none")
        self.assertIn("child_unavailable", pa.footer(result, "fr"))

    def test_four_calls_leave_no_tools_for_final_response_and_logs_exclude_records(self):
        batch = [call(identifier=str(i), arguments={"child": "Alex", "from": f"2026-10-{12 + i}",
                                                    "to": f"2026-10-{12 + i}"}) for i in range(4)]
        with self.assertLogs(pa.log, level="INFO") as logs:
            result, requests = self.request([{"outputs": batch}, final()])
        self.assertEqual(self.local.execute.call_count, 4)
        self.assertEqual(requests[-1].kwargs["json"]["tools"], [])
        self.assertNotIn("PRIVATE", str(logs.output))
        self.assertNotIn("Alex", str(logs.output))
        self.assertTrue(result["private"])

    def test_unknown_mutation_is_only_error_result_no_arbitrary_dispatch(self):
        with tempfile.TemporaryDirectory() as directory:
            real = pn.Pronote(directory)
            real.configure(True, {"Alex": {"account": "famille", "name": "PRIVATE FULLNAME"}})
            self.local = real
            result, requests = self.request([{"outputs": [call("pronote_enroll")]}, final("Action unavailable")])
        self.assertIn("unsupported_tool", requests[1].kwargs["json"]["inputs"][-1]["result"])
        self.assertIn("unsupported_tool", pa.footer(result, "en"))

    def test_terminal_private_history_footer_and_paginated_french_keys(self):
        result = engine.parse_response(final("Devoirs\n" * 30 + "Exercice 2."))
        result.update({"private": True, "pronote": [{"child": "Alex", "retrieved_at": "2026-10-11T20:00:00+02:00",
                                                     "cached": True, "stale": True, "truncated": False}]})
        with patch.object(pn, "backend", return_value=self.local), patch.object(engine, "answer", return_value=result):
            response = chat.call_answer("SECRET FICTION", [{"role": "user", "content": "Devoirs?"}],
                                        "en", ("conversations", True, 20), "")
        self.assertTrue(response["turn"]["private"])
        self.assertNotIn("cached", response["turn"]["content"])
        uart = FakeSerial(fn(chat.K_SUITE) * 10)
        terminal = object.__new__(chat.Term)
        terminal.s = uart
        with patch.object(chat.time, "sleep"):
            self.assertEqual(chat.show_response(terminal, chat.safe_display(response["display"]), "en"), "done")
        self.assertIn("possibly outdated", screen_text(uart.output))
        self.assertIn("PRONOTE Alex", screen_text(uart.output))

    def test_grade_table_becomes_narrow_vertical_list_in_preview_and_terminal_only(self):
        table = ("Notes d'Alex\n\n"
                 "| Matiere | Note | Coefficient | Date |\n"
                 "| :--- | ---: | ---: | --- |\n"
                 "| Mathematiques et sciences physiques | 17,5/20 | 0,5 | 2026-10-11 |\n"
                 "| Francais | Non noté | 1 | 2026-10-10 |\n")
        result = engine.parse_response(final(table))
        result.update({"private": True, "pronote": [{"child": "Alex", "period": "Trimestre 1",
                                                     "retrieved_at": "2026-10-12T01:30:00+02:00"}]})
        snapshot = json.dumps(result, ensure_ascii=False)
        with patch.object(pn, "backend", return_value=self.local), patch.object(engine, "answer", return_value=result):
            terminal = chat.call_answer("persona", [{"role": "user", "content": "Notes Alex ?"}],
                                        "fr", ("conversations", True, 20), "")
            with (patch.object(admin, "llm_provider", return_value="mistral"),
                  patch.object(admin, "mistral_key", return_value="test-secret-key"),
                  patch.object(admin, "read_env", return_value={})):
                preview = admin.llm_answer("persona", "Notes Alex ?")
        self.assertEqual(preview, terminal["display"])
        self.assertNotIn("|", preview)
        self.assertIn("Note: 17,5/20", preview)
        self.assertIn("Coefficient: 0,5", preview)
        self.assertIn("Non noté", preview)
        self.assertIn("Periode: Trimestre 1", preview)
        self.assertTrue(all(len(line) <= 39 for line in preview.splitlines()))
        self.assertEqual(terminal["turn"]["content"], table)
        self.assertEqual(json.dumps(result, ensure_ascii=False), snapshot)
        uart = FakeSerial(fn(chat.K_SUITE) * 10)
        term = object.__new__(chat.Term)
        term.s = uart
        with patch.object(chat.time, "sleep"):
            self.assertEqual(chat.show_response(term, chat.safe_display(preview), "fr"), "done")
        output = screen_text(uart.output)
        self.assertIn("17,5/20", output)
        self.assertIn("Non note", output)
        self.assertIn("Trimestre 1", output)

    def test_table_conversion_retains_escaped_pipes_code_blocks_and_long_word_tails(self):
        text = ("Subject | Grade\n--- | ---\nMaths\\|Physics | Absent\n\n"
                "```\n| literal | table |\n| --- | --- |\n| keep | code |\n```\n" + "Z" * 85)
        formatted = pa.narrow_text(text)
        self.assertIn("Subject: Maths|Physics", formatted)
        self.assertIn("Grade: Absent", formatted)
        self.assertIn("| literal | table |\n| --- | --- |\n| keep | code |", formatted)
        self.assertEqual(formatted.count("Z"), 85)
        self.assertTrue(all(len(line) <= 39 for line in formatted.splitlines()))
        public = engine.parse_response(final(text))
        self.assertEqual(pa.render(public, "fr"), text)

    def test_period_and_table_instructions_and_period_receipt_are_shared(self):
        self.local.execute.return_value["period"] = "Trimestre 2"
        result, requests = self.request([{"outputs": [call("pronote_get_grades", arguments={"child": "Alex"})]}, final()])
        prompt = requests[0].kwargs["json"]["instructions"]
        self.assertIn("OMIT period", prompt)
        self.assertIn("Never use Markdown tables", prompt)
        self.assertIn("Periode: Trimestre 2", pa.render(result, "fr"))


class AdminPronoteTests(unittest.TestCase):
    def setUp(self):
        admin.app.config["TESTING"] = True
        self.client = admin.app.test_client()
        self.temp = tempfile.TemporaryDirectory()
        self.local = pn.Pronote(Path(self.temp.name))
        self.backend = patch.object(pn, "backend", return_value=self.local)
        self.backend.start()

    def tearDown(self):
        self.backend.stop()
        self.temp.cleanup()

    def login(self):
        with self.client.session_transaction() as session:
            session["logged_in"] = True
        response = self.client.get("/pronote")
        with self.client.session_transaction() as session:
            self.csrf = session["pronote_csrf"]
        return response

    def test_authentication_csrf_no_secrets_in_page_or_session(self):
        self.assertEqual(self.client.get("/pronote").status_code, 302)
        response = self.login()
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertIn(b'autocapitalize="none"', response.data)
        self.assertIn(b'pattern="[A-Za-z0-9]', response.data)
        self.assertEqual(self.client.post("/pronote", data={"action": "configure"}).status_code, 403)
        with patch.object(self.local, "enroll", return_value=["PRIVATE CHILD"]) as enroll:
            response = self.client.post("/pronote", data={"csrf": self.csrf, "action": "enroll",
                                                         "account": "famille", "qr": json.dumps({"jeton": "SECRET QR"}),
                                                         "pin": "SECRET PIN"})
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"PRIVATE CHILD", response.data)
        self.assertNotIn(b"SECRET QR", response.data)
        self.assertNotIn(b"SECRET PIN", response.data)
        self.assertNotIn("SECRET", str(response.headers))
        enroll.assert_called_once()
        with self.client.session_transaction() as session:
            self.assertNotIn("SECRET", str(dict(session)))

    def test_admin_configuration_escaping_and_preview_same_tools(self):
        self.login()
        children = {"Alex": {"account": "famille", "name": "NOM <script>Alex</script>"}}
        response = self.client.post("/pronote", data={"csrf": self.csrf, "action": "configure",
                                                     "enabled": "on", "children": json.dumps(children)})
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"&lt;script&gt;", response.data)
        self.assertNotIn(b"NOM <script>", response.data)
        self.assertTrue(self.local.config()["enabled"])
        result = engine.parse_response(final("Devoir exact"))
        result["private"] = True
        with (patch.object(admin, "llm_provider", return_value="mistral"),
              patch.object(admin, "mistral_key", return_value="test-secret-key"),
              patch.object(admin, "read_env", return_value={}),
              patch.object(engine, "answer", return_value=result) as answer):
            self.assertEqual(admin.llm_answer("fiction", "Devoirs Alex ?"), "Devoir exact")
        self.assertIs(answer.call_args.kwargs["pronote"], self.local)

    def test_enrollment_page_shows_secret_free_failure_stage(self):
        self.login()
        error = pn.PronoteError("enrollment_failed", {"stage": "token_login", "error": "MFAError", "code": 25})
        with patch.object(self.local, "enroll", side_effect=error):
            response = self.client.post("/pronote", data={"csrf": self.csrf, "action": "enroll",
                                                         "account": "famille", "qr": '{"jeton":"SECRET QR"}',
                                                         "pin": "SECRET PIN"})
        self.assertIn(b"stage=token_login", response.data)
        self.assertIn(b"error=MFAError", response.data)
        self.assertNotIn(b"SECRET", response.data)
