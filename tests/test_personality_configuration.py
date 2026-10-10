"""Default updates must preserve existing local personalities and UI settings."""
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from services import minitel_chatgpt as chat


class PersonalityConfigurationTests(unittest.TestCase):
    def test_existing_local_preset_is_preserved(self):
        local = {
            "active": "custom",
            "presets": {
                "custom": {
                    "system": "My edited personality.",
                    "title_msg": "MY TITLE",
                    "question_msg": "My question?",
                    "loading_msg": "MY WAIT",
                    "title_art": [{"text": "CUSTOM", "font": "small"}],
                },
                "mainframe": {"system": "My previous mainframe edits."},
            },
        }
        with TemporaryDirectory() as directory:
            path = Path(directory) / "prompts.json"
            original = json.dumps(local, indent=2) + "\n"
            path.write_text(original)
            with (
                patch.object(chat, "PROMPTS_FILE", path),
                patch.object(chat, "load_knowledge", return_value=""),
            ):
                preset = chat.load_preset()
            self.assertEqual(path.read_text(), original)
        self.assertEqual(preset, (
            "My edited personality.", "MY TITLE", "My question?", "MY WAIT",
            [{"text": "CUSTOM", "font": "small"}],
        ))


if __name__ == "__main__":
    unittest.main()
