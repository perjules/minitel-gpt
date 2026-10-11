"""Paid smoke probe keeps captures free of keys, IDs, arguments and reasoning."""
import unittest
from unittest.mock import Mock, patch

from scripts import smoke_mistral_search as smoke


class SmokeTests(unittest.TestCase):
    def test_capture_preserves_final_entry_order_and_real_sources_without_private_state(self):
        result = smoke.redacted({"conversation_id": "secret-id", "outputs": [
            {"type": "message.output", "content": "Intermediate"},
            {"type": "tool.execution", "name": "web_search", "arguments": "private question",
             "id": "private-id", "completed_at": "today", "info": {"error": "private error"}},
            {"type": "message.output", "content": [
                {"type": "thinking", "thinking": [{"text": "private reasoning"}]},
                {"type": "text", "text": "Public answer"},
                {"type": "tool_reference", "tool": "web_search", "title": "Real source",
                 "url": "https://example.org/real", "unexpected": "private metadata"}]}],
            "usage": {"prompt_tokens": 3, "unexpected": "private usage"}})
        self.assertNotIn("private", str(result))
        self.assertNotIn("secret-id", str(result))
        self.assertEqual(result["outputs"][0]["content"], "Intermediate")
        self.assertEqual(result["outputs"][1]["info"], {"status": "failed"})
        self.assertEqual(result["outputs"][2]["content"][1]["url"], "https://example.org/real")
        self.assertEqual(result["usage"], {"prompt_tokens": 3})

    def test_no_key_stops_before_a_paid_request(self):
        with (patch.object(smoke, "load_dotenv"),
              patch.dict(smoke.os.environ, {"MISTRAL_KEY": ""}),
              patch.object(smoke.sys, "argv", ["smoke_mistral_search.py"]),
              patch.object(smoke.requests, "post") as post,
              patch.object(smoke.argparse.ArgumentParser, "error", side_effect=SystemExit(2)) as error,
              self.assertRaises(SystemExit)):
            smoke.main()
        error.assert_called_once()
        post.assert_not_called()

    def test_probe_three_bounded_calls_replays_sources_then_ordinary_answer(self):
        search = {"outputs": [
            {"type": "tool.execution", "name": "web_search", "completed_at": "today"},
            {"type": "message.output", "content": [
                {"type": "text", "text": "Release answer"},
                {"type": "tool_reference", "tool": "web_search", "title": "Python",
                 "url": "https://www.python.org/downloads/"}]}]}
        plain = {"outputs": [{"type": "message.output", "content": "Explanation"}]}
        responses = []
        for data in (search, plain, plain):
            response = Mock()
            response.json.return_value = data
            responses.append(response)
        with (patch.object(smoke, "load_dotenv"),
              patch.dict(smoke.os.environ, {"MISTRAL_KEY": "test-secret", "MISTRAL_ANSWER_API": "conversations",
                                            "MISTRAL_WEB_SEARCH": "1"}),
              patch.object(smoke.sys, "argv", ["smoke_mistral_search.py"]),
              patch.object(smoke.requests, "post", side_effect=responses) as post,
              patch("builtins.print")):
            self.assertEqual(smoke.main(), 0)
        self.assertEqual(post.call_count, 3)
        followup = post.call_args_list[1].kwargs["json"]
        self.assertFalse(followup["store"])
        self.assertEqual(followup["inputs"][1]["content"][1]["url"], "https://www.python.org/downloads/")


if __name__ == "__main__":
    unittest.main()
