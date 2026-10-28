"""Local smoke tests for the restricted Alexa gateway (no network calls)."""
import importlib.util
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

MODULE_PATH = Path(__file__).resolve().parents[1] / "integrations/alexa/voice_api.py"
spec = importlib.util.spec_from_file_location("voice_api", MODULE_PATH)
voice_api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(voice_api)


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.client = voice_api.app.test_client()
        voice_api._last_request = 0.0
        self.token = "t" * 48
        self.env = patch.dict(os.environ, {"ALEXA_API_TOKEN": self.token, "MISTRAL_KEY": "test", "MISTRAL_MODEL": "test-model"})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_unauthorized(self):
        r = self.client.post("/v1/ask", json={"question": "Bonjour"})
        self.assertEqual(r.status_code, 401)

    def test_rejects_empty_question(self):
        r = self.client.post("/v1/ask", headers={"Authorization": "Bearer " + self.token}, json={"question": ""})
        self.assertEqual(r.status_code, 400)

    @patch.object(voice_api.requests, "post")
    def test_answer(self, post):
        post.return_value.json.return_value = {"choices": [{"message": {"content": "Bonjour, périphérique."}}]}
        post.return_value.raise_for_status.return_value = None
        r = self.client.post("/v1/ask", headers={"Authorization": "Bearer " + self.token}, json={"question": "Bonjour"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["answer"], "Bonjour, périphérique.")
        self.assertEqual(post.call_args.kwargs["timeout"], (2, 5))


if __name__ == "__main__":
    unittest.main()
