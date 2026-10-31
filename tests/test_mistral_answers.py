"""Contract fixtures, factual preservation and locally owned request state."""
import copy
import json
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

from test_terminal_startup import FakeSerial, fn, screen_text

from services import minitel_chatgpt as chat
from services import mistral_answers as engine

FIXTURE = Path(__file__).parent / "fixtures" / "conversations_searched.json"


def fixture():
    return json.loads(FIXTURE.read_text())


def ordinary(text="17 x 23 = 391."):
    return {"outputs": [{"type": "message.output", "role": "assistant", "content": text}]}


class AnswerTests(unittest.TestCase):
    def request(self, data, history=None, enabled=True):
        response = Mock()
        response.json.return_value = data
        with patch.object(engine.requests, "post", return_value=response) as post:
            result = engine.answer(history or [{"role": "user", "content": "Search the web now"}],
                                   "en", "test-secret", "configured-model", enabled, 12)
        post.assert_called_once()
        return result, post.call_args

    def test_ordered_chunks_final_answer_and_deduplicated_references(self):
        result = engine.parse_response(fixture())
        self.assertEqual(result["status"], "searched")
        self.assertEqual(len(result["references"]), 2)
        self.assertEqual(result["references"][0]["title"], "First source")
        self.assertEqual(result["search"], [{"name": "web_search", "completed": True, "failed": False}])
        self.assertNotIn("Intermediate", result["text"])
        self.assertNotIn("Private", result["text"])
        rendered = engine.render(result)
        self.assertIn("uncertainty [1].", rendered)
        self.assertIn("source [2] and repeated [1].", rendered)
        self.assertIn("[1] example.org\n[2] example.net", rendered)
        self.assertIn("    return 17.25", rendered)
        self.assertIn("https://example.org/first?x=1", engine.render(result, full_urls=True))
        # Every factual text chunk is copied verbatim, without a rewriting pass.
        parts = [chunk["text"] for chunk in result["chunks"] if chunk["type"] == "text"]
        self.assertEqual(result["text"], "".join(parts))
        for part in parts:
            self.assertIn(part, rendered)

    def test_payload_character_with_factual_rules_and_unchanged_source_followup(self):
        first = engine.parse_response(fixture())
        history = [{"role": "user", "content": "Current value?"},
                   {"role": "assistant", "content": first["text"],
                    "chunks": first["chunks"], "references": first["references"]},
                   {"role": "user", "content": "What does the second source say?"}]
        snapshot = copy.deepcopy(history)
        result, call = self.request(ordinary("Uncertain."), history)
        self.assertEqual(call.args[0], engine.URL)
        payload = call.kwargs["json"]
        self.assertFalse(payload["store"])
        self.assertFalse(payload["stream"])
        self.assertEqual(payload["model"], "configured-model")
        self.assertEqual(payload["tools"], [{"type": "web_search"}])
        self.assertEqual(call.kwargs["timeout"], 12)
        self.assertEqual(payload["completion_args"]["max_tokens"], 700)
        self.assertIn(date.today().isoformat(), payload["instructions"])
        self.assertIn("Reply in English", payload["instructions"])
        voice = engine.CHAT_PROMPT_FILE.read_text(encoding="utf-8").strip()
        self.assertTrue(payload["instructions"].startswith(voice))
        self.assertIn(engine.PROMPT_FILE.read_text(encoding="utf-8"), payload["instructions"])
        self.assertNotIn(voice, json.dumps(payload["inputs"]))
        self.assertNotIn("conversation_id", payload)
        self.assertNotIn("Sources:", json.dumps(payload["inputs"]))
        self.assertNotIn("tool.execution", json.dumps(payload["inputs"]))
        self.assertEqual(payload["inputs"][1]["content"], first["chunks"])
        self.assertEqual(history, snapshot)
        self.assertEqual(result["status"], "ordinary")

    def test_sources_survive_multiple_terminal_pages_and_unicode_domains(self):
        data = fixture()
        data["outputs"][2]["content"][1]["text"] = "Long factual line\n" * 40
        data["outputs"][2]["content"][2]["url"] = "https://münchen.example/source"
        result = engine.parse_response(data)
        rendered = engine.render(result)
        self.assertIn("xn--mnchen-3ya.example", rendered)
        uart = FakeSerial(fn(chat.K_SUITE) * 10)
        term = object.__new__(chat.Term)
        term.s = uart
        with patch.object(chat.time, "sleep"):
            self.assertEqual(chat.show_response(term, chat.safe_display(rendered), "en"), "done")
        pages = bytes(uart.output).split(bytes([chat.FF]))
        self.assertGreater(len(pages), 3)
        final = screen_text(pages[-1])
        self.assertIn("Sources:", final)
        for domain in ("xn--mnchen-3ya.example", "example.net", "example.org"):
            self.assertIn(domain, final)
        self.assertTrue(all(len(line) < 40 for line in chat.wrap(chat.safe_display(rendered))))

    def test_no_search_off_switch_explicit_unexecuted_and_ordinary(self):
        result, call = self.request(ordinary())
        self.assertEqual(result["status"], "search_not_executed")
        result, call = self.request(ordinary(), enabled=False)
        self.assertEqual(call.kwargs["json"]["tools"], [])
        self.assertEqual(result["status"], "search_disabled")
        for question in ("Calculate 17 x 23", "Explain photosynthesis",
                         "Explain photosynthesis without web search", "Explique sans recherche"):
            result, _ = self.request(ordinary(), [{"role": "user", "content": question}])
            self.assertEqual(result["status"], "ordinary")
        for question in ("Cherche sur internet", "Recherche les nouvelles", "Look up today's news",
                         "Verifie sur le web", "Vérifie sur le web", "Search online"):
            self.assertTrue(engine.explicit_search(question))

    def test_partial_failure_and_insufficient_sources_are_distinct(self):
        data = fixture()
        data["outputs"][1]["completed_at"] = None
        self.assertEqual(engine.parse_response(data)["status"], "search_incomplete")
        data["outputs"][1]["completed_at"] = "today"
        data["outputs"][1]["info"] = {"status": "failed"}
        self.assertEqual(engine.parse_response(data)["status"], "search_incomplete")
        data["outputs"][1].pop("info")
        data["outputs"][2]["content"] = "No reliable evidence found."
        result = engine.parse_response(data)
        self.assertEqual(result["status"], "insufficient_sources")
        self.assertIn("No reliable evidence", result["text"])

    def test_followup_can_reuse_verified_sources_without_a_fresh_search(self):
        first = engine.parse_response(fixture())
        history = [{"role": "user", "content": "Price?"},
                   {"role": "assistant", "content": first["text"], "chunks": first["chunks"],
                    "references": first["references"]},
                   {"role": "user", "content": "Explain that price"}]
        citation = copy.deepcopy(first["chunks"][1])
        data = ordinary([{"type": "text", "text": "The earlier price is "}, citation])
        result, _ = self.request(data, history)
        self.assertEqual(result["status"], "ordinary")
        self.assertEqual(result["search"], [])
        self.assertTrue(result["reused_sources"])
        self.assertEqual(result["references"], [first["references"][0]])
        history[-1]["content"] = "Search the web to verify again"
        result, _ = self.request(data, history)
        self.assertEqual(result["status"], "search_not_executed")

    def test_unknown_followup_source_still_requires_real_search(self):
        first = engine.parse_response(fixture())
        history = [{"role": "assistant", "content": first["text"], "references": first["references"]},
                   {"role": "user", "content": "Explain"}]
        data = ordinary([{"type": "text", "text": "Claim"},
                         {"type": "tool_reference", "tool": "web_search", "title": "Invented",
                          "url": "https://unknown.example/claim"}])
        with self.assertRaisesRegex(ValueError, "Sources without observed search"):
            self.request(data, history)

    def test_price_search_then_multiple_followups_with_school_planner_enabled(self):
        backend = Mock()
        backend.tools.return_value = [{"type": "function", "function": {"name": "pronote_list_children"}}]
        backend.config.return_value = {"enabled": True, "children": {"Alex": {}}}
        first = engine.parse_response(fixture())
        followup = ordinary([{"type": "text", "text": "The previously quoted price "}, first["chunks"][1]])
        responses = []
        for data in (ordinary("Public"), fixture(), followup, followup, ordinary("Public"), ordinary("Other answer")):
            response = Mock()
            response.json.return_value = data
            responses.append(response)
        history = []
        with patch.object(engine.requests, "post", side_effect=responses) as post:
            for question in ("Price of a bicycle?", "Explain that price", "How far can it travel?"):
                candidate = history + [{"role": "user", "content": question}]
                result = engine.answer(candidate, "fr", "synthetic", "model", pronote=backend)
                self.assertFalse(result.get("private"))
                self.assertFalse(engine.notice(result["status"], "fr"))
                history = engine.bound_history(candidate + [{"role": "assistant", "content": result["text"],
                                                             "chunks": result["chunks"], "references": result["references"]}])
        self.assertEqual(post.call_count, 6)
        self.assertEqual(history[-1]["content"], "Other answer")
        backend.execute.assert_not_called()

    def test_private_sources_are_not_trusted_for_public_followups(self):
        first = engine.parse_response(fixture())
        history = [{"role": "user", "content": "PRIVATE QUESTION"},
                   {"role": "assistant", "content": "PRIVATE ANSWER", "private": True,
                    "references": first["references"]}, {"role": "user", "content": "Public"}]
        data = ordinary([{"type": "text", "text": "Claim"}, first["chunks"][1]])
        with self.assertRaisesRegex(ValueError, "Sources without observed search"):
            self.request(data, history)

    def test_malformed_empty_and_ambiguous_final_entries_fail_recoverably(self):
        bad = [None, {}, {"outputs": []}, ordinary(" "), ordinary(None),
               {"outputs": [ordinary()["outputs"][0]] * 2},
               {"outputs": [{"type": "function.call", "name": "unexpected"}]},
               ordinary([{"type": "image_url", "image_url": "unrequested"}])]
        for url in (None, "ftp://example.org", "https://secret@example.org", "https://example.org/\n"):
            data = fixture()
            data["outputs"][2]["content"][2]["url"] = url
            bad.append(data)
        data = fixture()
        data["outputs"].pop(1)
        bad.append(data)
        for data in bad:
            with self.subTest(data=data), self.assertRaises((ValueError, TypeError)):
                engine.parse_response(data)

    def test_api_failure_one_request_no_endpoint_retry_and_safe_logging(self):
        with (patch.object(engine.requests, "post", side_effect=engine.requests.Timeout("secret")) as post,
              self.assertLogs(engine.log, level="INFO") as logs,
              self.assertRaises(engine.requests.Timeout)):
            engine.answer([{"role": "user", "content": "private-question"}], "fr", "test-secret", "m")
        post.assert_called_once()
        self.assertIn("status=api_error", "".join(logs.output))
        self.assertNotIn("test-secret", "".join(logs.output))
        self.assertNotIn("private-question", "".join(logs.output))

    def test_bounded_complete_history_and_long_urls_code_pagination(self):
        history = []
        for index in range(15):
            history += [{"role": "user", "content": str(index)},
                        {"role": "assistant", "content": "x" * 4000}]
        bounded = engine.bound_history(history)
        self.assertLessEqual(len(bounded), 20)
        self.assertLessEqual(len(json.dumps(bounded, ensure_ascii=False)), 60000)
        self.assertEqual(bounded[0]["role"], "user")
        self.assertEqual(bounded[-1]["content"], "x" * 4000)
        self.assertEqual(engine.bound_history(history[-2:] + [{"role": "user", "content": "x" * 70000},
                                                            {"role": "assistant", "content": "a"}]), [])
        url = "https://example.org/" + "abcdefghijklmnopqrstuvwxyz" * 5
        lines = chat.wrap(url)
        self.assertEqual("".join(lines), url)
        self.assertTrue(all(len(line) < 40 for line in lines))
        self.assertEqual(chat.wrap("    return 17.25"), ["    return 17.25"])
        self.assertNotIn("\x1b", chat.safe_display("17.25\x1b[2J"))
        self.assertEqual(chat.safe_display("−5 µm ± 2°C ≤ 8 Ω"), "-5 um +/- 2degC <= 8 ohm")
        self.assertEqual(chat.safe_display("☃"), "\\u2603")

    def test_small_settings_and_explicit_rollback_do_not_change_other_adapters(self):
        self.assertEqual(engine.settings({}), ("conversations", True, 60))
        self.assertEqual(engine.settings({"MISTRAL_WEB_SEARCH": "0"})[1], False)
        for env in ({"MISTRAL_ANSWER_API": "invalid"}, {"MISTRAL_WEB_SEARCH": "maybe"},
                    {"MISTRAL_REQUEST_TIMEOUT": "nan"}, {"MISTRAL_REQUEST_TIMEOUT": "0"}):
            with self.assertRaises(ValueError):
                engine.settings(env)
        for provider, options in (("mistral", ("chat_completions", True, 60)),
                                  ("claude", ("conversations", True, 60)),
                                  ("linkup", ("conversations", True, 60))):
            with (patch.object(chat, "PROVIDER", provider),
                  patch.object(chat, "call_llm", return_value="original answer") as legacy,
                  patch.object(engine, "answer") as factual):
                result = chat.call_answer("personality", [{"role": "user", "content": "hi"}],
                                          "fr", options, "")
                self.assertEqual(result["display"], "original answer")
                legacy.assert_called_once()
                factual.assert_not_called()


if __name__ == "__main__":
    unittest.main()
