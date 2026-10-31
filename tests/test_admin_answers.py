"""Browser factual preview keeps real full URLs and shares the terminal adapter."""
import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from web_client import csrf_client

from services import admin_ui as admin
from services import mistral_answers as engine


class AdminAnswerTests(unittest.TestCase):
    def test_git_failures_keep_the_error_tuple_including_decoding_errors(self):
        for error in (FileNotFoundError("fixture"), subprocess.TimeoutExpired("git", 60),
                      UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")):
            with self.subTest(error=type(error).__name__), \
                    patch.object(admin.subprocess, "run", side_effect=error) as run:
                self.assertEqual(admin.git("status"), (1, "", str(error)))
                self.assertFalse(run.call_args.kwargs["check"])

    def test_log_read_failures_return_an_error_including_decoding_errors(self):
        for error in (FileNotFoundError("fixture"),
                      UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")):
            with self.subTest(error=type(error).__name__), \
                    patch.object(admin.subprocess, "run", side_effect=error) as run:
                self.assertIn("indisponible", admin.log_tail("chatgpt"))
                self.assertFalse(run.call_args.kwargs["check"])
                self.assertEqual(run.call_args.kwargs["timeout"], 7)

    def test_preview_shared_chat_voice_and_full_unicode_url_in_one_request(self):
        data = json.loads((Path(__file__).parent / "fixtures" / "conversations_searched.json").read_text())
        data["outputs"][2]["content"][2]["url"] = "https://example.org/données"
        response = Mock()
        response.json.return_value = data
        with (patch.object(admin, "llm_provider", return_value="mistral"),
              patch.object(admin, "mistral_key", return_value="test-secret"),
              patch.object(admin, "mistral_model", return_value="configured-model"),
              patch.object(admin, "read_env", return_value={"MISTRAL_ANSWER_API": "conversations"}),
              patch.object(engine.requests, "post", return_value=response) as call):
            text = admin.llm_answer("SECRET FICTION MF-83", "Question", knowledge="Known reference")
        self.assertIn("https://example.org/données", text)
        call.assert_called_once()
        self.assertNotIn("SECRET FICTION", str(call.call_args))
        payload = call.call_args.kwargs["json"]
        self.assertTrue(payload["instructions"].startswith(
            engine.CHAT_PROMPT_FILE.read_text(encoding="utf-8").strip()))
        self.assertIn("Known reference", payload["instructions"])
        self.assertEqual(payload["model"], "configured-model")
        self.assertFalse(payload["store"])

    def test_route_retains_full_urls_without_ascii_rewrite(self):
        admin.app.config["TESTING"] = True
        client = csrf_client(admin.app)
        with client.session_transaction() as session:
            session["logged_in"] = True
        # Match the existing authentication convention.
        with (patch.object(admin, "load_prompts", return_value={"presets": {"mainframe": {"system": "persona"}}}),
              patch.object(admin, "load_knowledge_blob", return_value=""),
              patch.object(admin, "llm_provider", return_value="mistral"),
              patch.object(admin, "read_env", return_value={}),
              patch.object(admin, "llm_answer", return_value="[1] https://example.org/données")):
            response = client.post("/test-preset", json={"preset_key": "mainframe", "message": "Question"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["answer"], "[1] https://example.org/données")


if __name__ == "__main__":
    unittest.main()
