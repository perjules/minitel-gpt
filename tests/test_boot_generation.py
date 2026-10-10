"""Boot output boundaries, provider completion handling and offline fallback."""
import unittest
from unittest.mock import Mock, patch

from services import minitel_chatgpt as chat


class BootGenerationTests(unittest.TestCase):
    def setUp(self):
        self.text = "\n".join(chat.UI["en"]["boot"][:-2])

    def test_variable_line_counts_and_crlf_are_accepted(self):
        # Include both sides of the old 16-20 boundary and multiple screens.
        for count in (1, 8, 15, 16, 20, 21, 45):
            rows = ["x" * 39] * count
            with self.subTest(count=count):
                self.assertEqual(chat.validate_boot("\r\n".join(rows) + "\r\n"), rows)

    def test_accents_and_long_diagnostics_are_rendered_without_truncation(self):
        text = "CORRECTION PARITÉ: TERMINÉE.\n\n" + "ABCDEFGHIJKLMNOPQRSTUVWXYZ" * 4
        rows = chat.validate_boot(text)
        self.assertEqual(rows[:2], ["CORRECTION PARITE: TERMINEE.", ""])
        self.assertEqual("".join(rows[2:]), "ABCDEFGHIJKLMNOPQRSTUVWXYZ" * 4)
        self.assertTrue(all(len(row) < chat.COLS for row in rows))
        sentence = "RELAY CHECK COMPLETE " * 5 + "END."
        rows = chat.validate_boot(sentence)
        self.assertEqual(" ".join(rows), sentence)
        self.assertTrue(all(len(row) < chat.COLS for row in rows))

    def test_controls_empty_text_and_unexpected_formats_are_rejected(self):
        for extra in ("\x00", "\x08", "\t", "\x0b", "\x0c", "\r", "\x13", "\x1b", "\x7f", "\x85"):
            with self.subTest(extra=repr(extra)), self.assertRaises(ValueError):
                chat.validate_boot(self.text + extra)
        for text in (None, [], {}, "", "\n\n", " " * 800, "☃"):
            with self.subTest(text=type(text)), self.assertRaises(ValueError):
                chat.validate_boot(text)
        for prefix in ("```", "# Boot", "* step", "- step"):
            with self.subTest(prefix=prefix), self.assertRaises(ValueError):
                chat.validate_boot(prefix + "\n" + self.text)

    def test_model_cannot_supply_function_key_instructions(self):
        for key in ("SUITE", "ENVOI", "SOMMAIRE", "GUIDE", "RETOUR", "CORRECTION"):
            for instruction in (f"Press {key.lower()}", f"Appuyez sur {key}"):
                with self.subTest(instruction=instruction), self.assertRaises(ValueError):
                    chat.validate_boot(instruction)

    def test_missing_keys_and_search_provider_make_no_request(self):
        with patch.object(chat.requests, "post") as post:
            for provider in ("mistral", "claude", "linkup"):
                with (
                    self.subTest(provider=provider),
                    patch.object(chat, "PROVIDER", provider),
                    patch.object(chat, "MISTRAL_KEY", ""),
                    patch.object(chat, "ANTHROPIC_KEY", ""),
                ):
                    self.assertEqual(chat.prepare_boot("en"), chat.UI["en"]["boot"])
            # Even a configured search account must never receive fictional boot queries.
            with patch.object(chat, "PROVIDER", "linkup"), patch.object(chat, "LINKUP_KEY", "test"):
                self.assertEqual(chat.prepare_boot("fr"), chat.UI["fr"]["boot"])
            post.assert_not_called()

    def test_missing_or_invalid_prompt_data_falls_back(self):
        for error in (FileNotFoundError(), ValueError("bad JSON")):
            with (
                self.subTest(error=error),
                patch.object(chat, "PROVIDER", "mistral"),
                patch.object(chat, "MISTRAL_KEY", "test"),
                patch.object(chat.Path, "read_text", side_effect=error),
                patch.object(chat.requests, "post") as post,
                patch.object(chat.log, "warning"),
            ):
                self.assertEqual(chat.prepare_boot("fr"), chat.UI["fr"]["boot"])
                post.assert_not_called()

    def test_provider_completion_reasons_and_existing_chat_defaults(self):
        for provider, function, response_data, field, complete, incomplete in (
            ("mistral", chat.call_mistral,
             {"choices": [{"message": {"content": self.text}, "finish_reason": "stop"}]},
             "finish_reason", "stop", ("length", "tool_calls", None)),
            ("claude", chat.call_claude,
             {"content": [{"type": "text", "text": self.text}], "stop_reason": "end_turn"},
             "stop_reason", "end_turn", ("max_tokens", "tool_use", "pause_turn", None)),
        ):
            with self.subTest(provider=provider):
                response = Mock()
                response.json.return_value = response_data
                with patch.object(chat.requests, "post", return_value=response) as post:
                    self.assertEqual(function("Boot prompt", [], timeout=8, require_complete=True), self.text)
                    self.assertEqual(post.call_args.kwargs["timeout"], 8)
                    target = response_data["choices"][0] if provider == "mistral" else response_data
                    for reason in incomplete:
                        target[field] = reason
                        with self.subTest(reason=reason), self.assertRaises(ValueError):
                            function("Boot prompt", [], timeout=8, require_complete=True)
                    target[field] = complete
                    self.assertEqual(function("Chat prompt", []), self.text.strip())
                    self.assertEqual(post.call_args.kwargs["timeout"], 30)

    def test_claude_generation_uses_its_configuration(self):
        response = Mock()
        response.json.return_value = {
            "content": [{"type": "text", "text": self.text}], "stop_reason": "end_turn",
        }
        with (
            patch.object(chat, "PROVIDER", "claude"),
            patch.object(chat, "ANTHROPIC_KEY", "test-claude"),
            patch.object(chat, "CLAUDE_MODEL", "claude-haiku-4-5"),
            patch.object(chat.requests, "post", return_value=response) as post,
            patch.object(chat.log, "info"),
        ):
            rows = chat.prepare_boot("en")
        post.assert_called_once()
        self.assertEqual(post.call_args.args[0], chat.ANTHROPIC_URL)
        request = post.call_args.kwargs
        self.assertEqual(request["json"]["model"], "claude-haiku-4-5")
        self.assertEqual(request["headers"]["x-api-key"], "test-claude")
        self.assertEqual(request["timeout"], chat.BOOT_TIMEOUT)
        self.assertEqual(rows[-1], chat.UI["en"]["boot"][-1])
        self.assertLess(len(rows), chat.SCREEN_ROWS)

if __name__ == "__main__":
    unittest.main()
