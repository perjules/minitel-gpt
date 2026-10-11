"""Conversations function-call contracts and private school / public web isolation."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from test_terminal_startup import FakeSerial, fn, screen_text

from services import admin_ui as admin
from services import minitel_chatgpt as chat
from services import mistral_answers as engine
from services import pronote as pn
from services import pronote_answers as pa


def final(text="Maths : exercice 2 pour demain."):
    return {"outputs": [{"type": "message.output", "role": "assistant", "content": text}]}


def call(name="pronote_get_homework", identifier="call-1", arguments=None):
    return {"type": "function.call", "name": name, "tool_call_id": identifier,
            "arguments": arguments or {"child": "Alex", "from": "2026-10-12", "to": "2026-10-12"}}


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
        self.local.execute.assert_not_called()
        self.assertNotIn("private", result)

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
        self.assertEqual(self.local.execute.call_count, 2)

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
