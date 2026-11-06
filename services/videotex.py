"""Small Minitel G2 repertoire, not a general Unicode codec.

STUM: SS2 + accent selector + G0 base; uppercase accented output is unavailable.
https://jbellue.github.io/stum1b/#codage-des-caract%C3%A8res-alphanum%C3%A9riques
"""

import unicodedata

SS2 = 0x19
ACCENTS = {0x41: "\u0300", 0x42: "\u0301", 0x43: "\u0302", 0x48: "\u0308", 0x4B: "\u0327"}
GLYPHS = {"œ": b"\x19\x7a", "Œ": b"\x19\x6a", "£": b"\x19\x23"}
for _char in "àâäéèêëîïöôùûüç":
    _base, _mark = unicodedata.normalize("NFD", _char)
    _selector = next(key for key, value in ACCENTS.items() if value == _mark)
    GLYPHS[_char] = bytes([SS2, _selector, ord(_base)])
DECODE = {value: key for key, value in GLYPHS.items()}
# Native uppercase accent keystrokes remain exact internally, even though their
# display fallback is the unaccented capital on this generation of Minitel.
for _sequence, _char in list(DECODE.items()):
    if len(_sequence) == 3:
        DECODE[_sequence[:2] + _sequence[2:].upper()] = _char.upper()
INPUT_CODES = frozenset(map(ord, DECODE.values()))


def input_character(code):
    return 0x20 <= code <= 0x7E or code in INPUT_CODES


def encode(text):
    """Each mapped character is one cell; all emitted bytes remain seven-bit."""
    return b"".join(GLYPHS.get(char, char.encode("ascii", errors="replace")) for char in text)


def decode(sequence):
    return DECODE.get(bytes(sequence), "?")
