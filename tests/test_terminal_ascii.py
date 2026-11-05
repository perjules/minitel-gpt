"""Readable display fallback without changing provider data or keyboard decoding."""

import pytest
from test_rose_echo import submitted
from test_terminal_display import VisibleUART

from services import minitel_chatgpt as chat
from services.terminal_documents import read


@pytest.mark.parametrize("source,expected", [
    ("école → devoirs ← demain ↔ aujourd’hui", "ecole -> devoirs <- demain <-> aujourd'hui"),
    ("Résultat ⇒ ½ + ¼ = ¾", "Resultat => 1/2 + 1/4 = 3/4"),
    ("☃🙂", "??"),
    ("15 € / 10 £", "15 EUR / 10 GBP"),
    ("Salle Ａ, ①", "Salle A, 1"),
    ("A\x1b[2J B\x00", "A\\x1b[2J B\\x00"),
])
def test_ascii_display_conversion_is_readable_safe_and_idempotent(source, expected):
    assert chat.safe_display(source) == expected
    assert chat.safe_display(expected) == expected
    assert expected.isascii()


@pytest.mark.parametrize("title", ["3615 IA / COURRIER", "3615 IA / PRONOTE"])
def test_feature_view_uses_short_fallback_and_preserves_source(monkeypatch, title):
    monkeypatch.setattr(chat.time, "sleep", lambda _: None)
    source = "Français → Salle ☃\n" * 30
    uart = VisibleUART(submitted("LIRE 1"))
    term = chat.Term(uart)
    assert read(term, source, title=title) == ("LIRE 1", "envoi")
    assert term.view.text == source
    assert len(term.view.pages) == 2
    assert uart.lines()[1].startswith("Francais -> Salle ?")
    assert b"\\u2192" not in uart.output and b"\\u2603" not in uart.output
