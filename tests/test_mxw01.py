"""Offline MXW01 protocol regression tests."""
import unittest
from printing.mxw01 import command, crc8, pack_image, response_packet


class MXW01ProtocolTests(unittest.TestCase):
    def test_crc(self):
        self.assertEqual(crc8(b""), 0)
        self.assertEqual(crc8(b"\\x00"), 0)

    def test_status_command(self):
        self.assertEqual(command(0xA1, b"\\x00"), bytes.fromhex("2221a100010000ff"))

    def test_print_request(self):
        self.assertEqual(command(0xA9, bytes.fromhex("01003000"), checksum=False),
                         bytes.fromhex("2221a90004000100300000ff"))

    def test_response(self):
        self.assertEqual(response_packet(bytes.fromhex("2221a900010000ff")), (0xA9, b"\\x00"))
        self.assertIsNone(response_packet(b"bad"))

    def test_pixel_packing(self):
        from PIL import Image
        image = Image.new("1", (384, 1), 1)
        image.putpixel((0, 0), 0)
        image.putpixel((7, 0), 0)
        packed = pack_image(image)
        self.assertEqual(len(packed), 48)
        self.assertEqual(packed[0], 0x81)
        self.assertEqual(packed[1:], bytes(47))


if __name__ == "__main__":
    unittest.main()
