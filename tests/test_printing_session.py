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

    def test_long_document_prints_all_sections(self):
        from printing import PrintDocument, TextBlock
        from printing.render import render_document_pages

        # Three screenfuls of distinct content must survive paper sectioning.
        content = "\n".join(f"PRONOTE LIGNE {index:03d} NOTE {index}/20" for index in range(100))
        pages = render_document_pages(PrintDocument("3615 IA", (TextBlock(content),)))
        self.assertGreater(len(pages), 1)
        self.assertTrue(all(page.width == 384 and page.height <= 600 for page in pages))
        # A text document is never silently truncated at the old 6000px limit.
        self.assertGreater(sum(page.height for page in pages), 600)

    def test_document_uses_one_ble_session(self):
        from unittest.mock import AsyncMock

        from printing import PrintDocument, TextBlock
        document = PrintDocument("3615 IA", (TextBlock("ligne\n" * 80),))
        with patch("printing.mxw01.MXW01") as driver:
            driver.return_value.print_images = AsyncMock()
            events = []
            printing_session.print_with_progress(document, events.append)
            self.assertEqual(driver.call_count, 1)
            self.assertEqual(driver.return_value.print_images.await_count, 1)
            sections = driver.return_value.print_images.await_args.args[0]
            self.assertGreater(len(sections), 1)
            self.assertTrue(all(section.height <= 600 for section in sections))

    def test_extension_is_optional(self):
        with patch.dict("os.environ", {"PRINTING_ENABLED": "0"}):
            self.assertFalse(printing_session.installed())


if __name__ == "__main__":
    unittest.main()
