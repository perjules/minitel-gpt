"""Printing routing tests: no BLE hardware or model needed."""
import unittest
from unittest.mock import patch
from services import printing_session


class PrintSessionTests(unittest.TestCase):
    def test_direct_command(self):
        self.assertEqual(printing_session.intent("IMPRIMER"), "direct")
        self.assertEqual(printing_session.intent("imprimer  "), "direct")
        self.assertEqual(printing_session.intent("imprime cette réponse"), "confirm")
        self.assertEqual(printing_session.intent("imprimer une image de chat"), "image")
        self.assertIsNone(printing_session.intent("Quelle est la température ?"))

    def test_approval_and_single_use(self):
        session = printing_session.Session()
        session.remember("Bonjour")
        approval = session.prepare()
        self.assertIsNotNone(approval)
        self.assertEqual(session.take_confirmed("OUI").blocks[0].text, "Bonjour")
        self.assertIsNone(session.take_confirmed("OUI"))

    def test_cancel_and_reset(self):
        session = printing_session.Session()
        session.remember("Bonjour")
        session.prepare()
        session.cancel()
        self.assertIsNone(session.take_confirmed("OUI"))
        session.reset()
        self.assertIsNone(session.last_text)

    def test_extension_is_optional(self):
        with patch.dict("os.environ", {"PRINTING_ENABLED": "0"}):
            self.assertFalse(printing_session.installed())


if __name__ == "__main__":
    unittest.main()
