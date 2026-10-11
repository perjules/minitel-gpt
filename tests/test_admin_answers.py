"""Browser factual preview keeps real full URLs and shares the terminal adapter."""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from services import admin_ui as admin
from services import mistral_answers as engine


class AdminAnswerTests(unittest.TestCase):
    def test_preview_shared_adapter_neutral_context_and_full_unicode_url(self):
        data = json.loads((Path(__file__).parent / "fixtures" / "conversations_searched.json").read_text())
        data["outputs"][2]["content"][2]["url"] = "https://example.org/données"
        result = engine.parse_response(data)
        with (patch.object(admin, "llm_provider", return_value="mistral"),
              patch.object(admin, "mistral_key", return_value="test-secret"),
              patch.object(admin, "mistral_model", return_value="configured-model"),
              patch.object(admin, "read_env", return_value={"MISTRAL_ANSWER_API": "conversations"}),
              patch.object(engine, "answer", return_value=result) as call):
            text = admin.llm_answer("SECRET FICTION MF-83", "Question", knowledge="Known reference")
        self.assertIn("https://example.org/données", text)
        self.assertNotIn("SECRET FICTION", str(call.call_args))
        self.assertEqual(call.call_args.args[-1], "Known reference")

    def test_route_retains_full_urls_without_ascii_rewrite(self):
        admin.app.config["TESTING"] = True
        client = admin.app.test_client()
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
