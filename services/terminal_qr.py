"""Local QR matrices and native joined G1 output; no URL fetching or shortening.

STUM G1 chart: https://jbellue.github.io/stum1b/img/p101/1.svg
Bits 0..5 are top-left, top-right, middle-left, middle-right, bottom-left,
bottom-right. Wire bit 5 is unused; the sixth pattern bit uses wire bit 6.
"""

from dataclasses import dataclass
from urllib.parse import urlsplit

import segno

DEMO_URL = "https://example.org"


def command(text):
    """None means ordinary input; zero means a malformed local LINK command."""
    words = text.split()
    if not words or words[0].casefold() not in ("link", "lien"):
        return None
    return int(words[1]) if len(words) == 2 and words[1].isascii() and words[1].isdigit() and len(words[1]) < 5 else 0


def mosaic_code(mask):
    if not 0 <= mask < 64:
        raise ValueError("Mosaic needs six bits")
    return 0x20 | (mask & 31) | ((mask & 32) << 1)


def mosaic_mask(code):
    return (code & 31) | ((code & 64) >> 1)


def mosaic_character(mask):
    # Unicode Symbols for Legacy Computing omit the existing half/full blocks.
    if mask in (0, 21, 42, 63):
        return {0: " ", 21: "▌", 42: "▐", 63: "█"}[mask]
    return chr(0x1FB00 + mask - 1 - (mask > 21) - (mask > 42))


@dataclass(frozen=True)
class Code:
    matrix: tuple
    full_screen: bool


def make(url, geometry):
    """Preserve the exact URL, including query/fragment and Unicode, in the QR."""
    if not isinstance(url, str) or not url or len(url) > 4096 or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in url):
        raise ValueError("Lien invalide")
    try:
        parsed = urlsplit(url)
        valid = parsed.scheme in ("http", "https") and parsed.hostname and not parsed.username and not parsed.password
        _ = parsed.port  # Reject malformed ports too, without connecting anywhere.
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("Lien invalide")
    try:
        qr = segno.make_qr(url, error="M", boost_error=False, encoding="utf-8", eci=not url.isascii())
    except segno.DataOverflowError as error:
        raise ValueError("Lien trop long pour un QR Minitel") from error
    matrix = tuple(tuple(row) for row in qr.matrix_iter(border=4))
    size = len(matrix)
    if size > geometry.width * 2 or size > (geometry.rows - 1) * 3:
        raise ValueError("Lien trop long pour un QR Minitel")
    return Code(matrix, size > geometry.content_rows * 3)


def draw(term, code):
    """One G1 byte per 2x3 block; white quiet zone, black modules, no C40 wrap."""
    geometry = term.geometry
    matrix = code.matrix
    size = len(matrix)
    first, height = (1, geometry.rows - 1) if code.full_screen else (geometry.content_first, geometry.content_rows)
    x0, y0 = (geometry.width * 2 - size) // 2, (height * 3 - size) // 2
    # G1 makes every mosaic a background delimiter. Explicit FG/BG and SI on
    # both sides prevent zones or a cancelled drawing from leaking into G0 text.
    try:
        for row in range(y0 // 3, (y0 + size + 2) // 3):
            cells = bytearray()
            left = x0 // 2
            for column in range(left, (x0 + size + 1) // 2):
                mask = 0
                for bit in range(6):
                    x, y = column * 2 + bit % 2 - x0, row * 3 + bit // 2 - y0
                    if 0 <= x < size and 0 <= y < size and not matrix[y][x]:
                        mask |= 1 << bit  # Lit blocks form the white paper.
                cells.append(mosaic_code(mask))
            term.w(geometry.position(first + row, left + 1) + b"\x0e\x1b\x59\x1b\x47\x1b\x50" + cells + b"\x0f")
    finally:
        term.w(b"\x0f\x1b\x5c\x1b\x47\x1b\x50")
