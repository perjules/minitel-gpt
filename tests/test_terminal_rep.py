"""REP saves wire bytes without changing logical cells, styles or timing budgets."""

import pytest
from test_terminal_display import VisibleUART

from services import minitel_chatgpt as chat
from services.console_terminal import Screen
from services.terminal_display import STANDARD, View, encode_runs


@pytest.mark.parametrize("length", [0, 1, 2, 3, 4, 38, 39, 40, 63, 64, 65, 66, 67, 68, 128])
@pytest.mark.parametrize("character", [" ", "-", "A"])
def test_rep_literal_equivalence_with_split_controls(length, character):
    literal = (character * length).encode()
    encoded = encode_runs(character * length)
    assert len(encoded) <= len(literal)
    if 4 <= length <= 64:
        assert encoded == character.encode() + bytes([0x12, 0x40 + length - 1])
    for inverse in (False, True):
        prefix = STANDARD.position(5) + b"\x1b\x42" + (b"\x1b\x5d" if inverse else b"\x1b\x5c")
        left, right = Screen(), Screen()
        left.write(prefix + literal)
        for code in prefix + encoded:
            right.write(bytes([code]))
        assert left.rows == right.rows
        assert left.styles == right.styles
        assert (left.row, left.column) == (right.row, right.column)


@pytest.mark.parametrize("text", ["\n", "\x18", "☃", "\x1bA"])
def test_rep_rejects_controls_and_unencoded_accents(text):
    with pytest.raises((ValueError, UnicodeEncodeError)):
        encode_runs(text)


def test_compressed_view_matches_literal_view_and_saves_bytes(monkeypatch):
    monkeypatch.setattr(chat.time, "sleep", lambda _: None)
    from services import terminal_display
    compressed = VisibleUART()
    term = chat.Term(compressed)
    View(term, ("A" * 39 + "\n") * 18).install()
    compressed_cache = dict(term.view.cache)
    literal = VisibleUART()
    monkeypatch.setattr(terminal_display, "encode_runs", lambda text: text.encode("ascii"))
    other = chat.Term(literal)
    View(other, ("A" * 39 + "\n") * 18).install()
    assert compressed.screen.rows == literal.screen.rows
    assert compressed.screen.styles == literal.screen.styles
    assert compressed_cache == other.view.cache
    assert len(literal.output) - len(compressed.output) >= 18 * 36
    assert all(row[-1] == " " for row in compressed.screen.rows)
