"""Local display diagnostic: synthetic answer text, no API, raw UART or storage.

Run from SSH with: python -m services.terminal_diagnostics
On the Minitel, type TEST UNICODE in ordinary chat, then ENVOI.
"""


def unicode_command(text):
    return " ".join(text.split()).casefold() == "test unicode"


def scroll_command(text):
    return " ".join(text.split()).casefold() == "test scroll"


def test_scroll():
    """Three loaded pages; no inference, persistence or external operation."""
    return {"display": "\n".join(f"Journal {number:02d} : été au central MF-83." for number in range(1, 55)),
            "scrolling": True, "turn": None}


def test_unicode():
    """A local tool result using the same display payload as an AI answer."""
    lines = [
        "TEST UNICODE - sortie applicative",
        "01 ASCII: Aa Zz 0123456789 @ #",
        "02 FR: é è ê ë à â ù û ü î ï ô ç",
        "03 CAPS: É È Ê Ë À Â Ù Û Ü Î Ï Ô Ç",
        "04 NFD: e\u0301 a\u0300 u\u0308 c\u0327",
        "05 ISOLES: \u0301 \u0300 \u0308 \u0327",
        "06 LIG: œ Œ æ Æ ß ø Ø",
        "07 FLECHES: → ← ↔ ↑ ↓ ⇒",
        "08 MATH: − ± × ÷ ≤ ≥ ≠ Ω µ °",
        "09 DEVISES: € £ $ ¥",
        "10 FRACTIONS: ½ ¼ ¾ ⅓ ² ³",
        "11 PONCT: «» “ ” ’ – — … •",
        "12 ESPACES: A\u00a0B A\u202fB A\u2009B",
        "13 LATIN: Ł ł Đ đ Þ þ ñ å",
        "14 AUTRES: α β Ж 中 日",
        "15 EMOJI: 🙂 ☃ 🌹 ❤️ 👍🏽",
        "16 CADRES: ┌─┐ │ └─┘ ═ ░ █",
        r"17 LITTERAL: \u00e9",
    ]
    return {"display": "\n".join(lines), "turn": None}


if __name__ == "__main__":
    print(test_unicode()["display"])
