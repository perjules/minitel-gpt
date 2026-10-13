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

    def test_unicode_accents_and_symbols(self):
        from printing.render import render_document_pages
        from PIL import ImageChops
        samples = (
            "é è ê ë à â ù û î ï ô ç œ É Ç — €",
            "e e e e a a u u i i o c oe E C - EUR",
        )
        images = [
            render_document_pages(PrintDocument("Titre", (TextBlock(sample),)))[0]
            for sample in samples
        ]
        self.assertEqual(images[0].width, 384)
        overlap = min(images[0].height, images[1].height)
        self.assertIsNotNone(ImageChops.difference(
            images[0].convert("L").crop((0, 0, 384, overlap)),
            images[1].convert("L").crop((0, 0, 384, overlap)),
        ).getbbox())

    def test_height_limit(self):
        with self.assertRaises(ValueError):
            render_document(PrintDocument("Title", (TextBlock("hello " * 1000),)), max_height=64)


if __name__ == "__main__":
    unittest.main()
