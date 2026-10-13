from pathlib import Path
import tempfile
import unittest
from printing import ImageBlock, PrintApproval, PrintDocument, TextBlock, render_document


class PrintingTests(unittest.TestCase):
    def test_approval_is_explicit_and_single_use(self):
        document = PrintDocument("Test", (TextBlock("Hello"),))
        approval = PrintApproval(document)
        self.assertIsNone(approval.confirm(user_input="OUI", token="wrong"))
        self.assertIsNone(approval.confirm(user_input="yes", token=approval.token))
        self.assertIs(approval.confirm(user_input="oui", token=approval.token), document)
        self.assertIsNone(approval.confirm(user_input="OUI", token=approval.token))

    def test_cancel(self):
        approval = PrintApproval(PrintDocument("Test", ()))
        approval.cancel()
        self.assertIsNone(approval.confirm(user_input="OUI", token=approval.token))

    def test_render_image_and_text(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "image.png"
            Image.new("RGB", (100, 100), "black").save(path)
            doc = PrintDocument("Title", (TextBlock("Some words"), ImageBlock(path)))
            image = render_document(doc)
            self.assertEqual(image.width, 384)
            self.assertEqual(image.mode, "1")
            self.assertGreater(image.height, 384)

    def test_height_limit(self):
        with self.assertRaises(ValueError):
            render_document(PrintDocument("Title", (TextBlock("hello " * 1000),)), max_height=64)


if __name__ == "__main__":
    unittest.main()
