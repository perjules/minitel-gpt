"""Exact source URLs, real G1 wire patterns, local editor and recovery gates."""

import unicodedata
from unittest.mock import Mock

import pytest
import test_terminal_display as terminal_tests
import test_terminal_serial_timing as timing_tests
import test_terminal_startup as startup
from test_rose_echo import submitted
from test_terminal_display import assert_gray_input, long_answer
from test_terminal_startup import fn, login_keys

from services import minitel_chatgpt as chat
from services import terminal_display as display
from services import terminal_qr as qr
from services.console_terminal import Screen

REFS = [{"url": qr.DEMO_URL, "title": "First"}, {"url": "https://example.net/?a=1&b=2#part", "title": "Second"}]
hardware = terminal_tests.hardware
paced = timing_tests.paced


def glyph_mask(char):
    """Independent Unicode names give positions 1..6, in reading order."""
    if char in " ▌▐█":
        return {" ": 0, "▌": 21, "▐": 42, "█": 63}[char]
    name = unicodedata.name(char)
    assert name.startswith("BLOCK SEXTANT-")
    return sum(1 << (int(bit) - 1) for bit in name.removeprefix("BLOCK SEXTANT-"))


def test_g1_matches_stum_single_blocks_and_full_cell():
    assert [qr.mosaic_code(1 << bit) for bit in range(6)] == [0x21, 0x22, 0x24, 0x28, 0x30, 0x60]
    assert qr.mosaic_code(63) == 0x7F
    for mask in range(64):
        screen = Screen()
        # Fragmented controls and REP must keep the same graphics repertoire.
        for code in b"\x0e" + bytes([qr.mosaic_code(mask)]) + b"\x12\x41\x0fA":
            screen.write(bytes([code]))
        assert [glyph_mask(cell) for cell in screen.rows[0][:2]] == [mask, mask]
        assert screen.rows[0][2] == "A"
        assert not screen.mosaic
    with pytest.raises(ValueError):
        qr.mosaic_code(64)


def test_qr_exact_pixels_quiet_zone_polarity_and_safe_columns(hardware):
    term, uart = hardware()
    view = display.View(term, long_answer(), references=REFS).install()
    original = uart.lines()
    before = len(uart.output)
    view.open_link(1)
    code = view.qr
    matrix = code.matrix
    size = len(matrix)
    assert not code.full_screen
    assert all(not cell for row in matrix[:4] + matrix[-4:] for cell in row)
    assert all(not cell for row in matrix for cell in row[:4] + row[-4:])
    x0, y0 = (78 - size) // 2, (54 - size) // 2
    for y in range(54):
        for x in range(78):
            mask = glyph_mask(uart.screen.rows[1 + y // 3][x // 2])
            lit = bool(mask & (1 << ((y % 3) * 2 + x % 2)))
            expected = 0 <= x - x0 < size and 0 <= y - y0 < size and not matrix[y - y0][x - x0]
            assert lit == expected
    assert uart.lines()[0] == original[0]
    assert uart.lines()[21:] == original[21:]
    assert all(row[39] == " " for row in uart.screen.rows)
    assert (uart.screen.row, uart.screen.column) == (21, 2)
    assert not uart.screen.mosaic
    assert len(uart.output[before:]) < 1400  # Under 12s of 1200-baud 7E1 wire time.
    view.close_qr()
    assert uart.lines() == original
    assert_gray_input(uart)


def test_exact_unicode_and_ascii_url_reach_encoder_without_display_conversion(monkeypatch):
    make = qr.segno.make_qr
    spy = Mock(wraps=make)
    monkeypatch.setattr(qr.segno, "make_qr", spy)
    for url in ("https://example.org/path?q=a%20b&x=2#part", "https://example.org/été?q=œ#été"):
        qr.make(url, display.STANDARD)
        assert spy.call_args.args == (url,)
        assert spy.call_args.kwargs["eci"] == (not url.isascii())


@pytest.mark.parametrize("url", [None, "", "javascript:alert(1)", "file:///tmp/a", "https://u:p@example.org", "https://example.org/\nnext", "https://example.org/ a", "https://[", "https://example.org:bad", "https://", "https://example.org/" + "x" * 5000])
def test_invalid_urls_leave_text_and_pages_intact(hardware, url):
    term, uart = hardware()
    view = display.View(term, long_answer(), references=[{"url": url}]).install()
    original = uart.lines()[1:20]
    view.open_link(1)
    assert view.qr is None and view.page == 0
    assert uart.lines()[1:20] == original
    assert "Lien invalide" in uart.lines()[20]


def test_long_url_full_screen_return_and_overflow(hardware):
    term, uart = hardware()
    code = qr.make("https://example.org/" + "a" * 130, display.STANDARD)
    assert code.full_screen
    view = display.View(term, long_answer(), references=[{"url": "https://example.org/" + "a" * 130}]).install()
    view.move(1)
    view.buffer = list("DRAFT")
    view.input()
    original = uart.lines()
    view.open_link(1)
    assert view.qr.full_screen and not uart.screen.cursor_visible
    assert uart.lines()[23].startswith("QR - RETOUR")
    view.close_qr()
    assert uart.lines() == original
    assert view.page == 1 and view.buffer == list("DRAFT")
    assert_gray_input(uart)
    with pytest.raises(ValueError, match="trop long"):
        qr.make("https://example.org/" + "a" * 300, display.STANDARD)


def test_link_commands_consumed_locally_recall_and_feature_paging_preserved(hardware):
    keys = submitted("link 2") + fn(chat.K_RETOUR) + b"DRAFT" + fn(chat.K_SUITE) + submitted(" END")
    term, uart = hardware(keys)
    view = display.View(term, long_answer(), references=REFS).install()
    view.page = 1
    view.draw()
    term.prompt_history.add("Previous prompt")
    assert view.read(recall=True) == ("DRAFT END", "envoi")
    assert view.page == 2  # RETOUR closed QR at page 2; SUITE then moved forward.
    assert view.references == tuple(REFS)
    assert term.prompt_history.items == ["Previous prompt"]
    assert uart.lines()[21].startswith("> DRAFT END")
    assert all(copies == 0 for _, copies in uart.echo)
    assert not uart.screen.mosaic


def test_opening_qr_does_not_acknowledge_unseen_review_pages(hardware):
    term, _ = hardware(submitted("link 1") + fn(chat.K_RETOUR) + submitted("SEND"))
    reviewed = Mock()
    view = display.View(term, long_answer(), references=REFS, reviewed=reviewed).install()
    assert view.read() == ("SEND", "envoi")
    assert view.seen == {0}
    reviewed.assert_not_called()


@pytest.mark.parametrize("full_screen", [False, True])
def test_new_view_and_busy_clear_all_mosaic_cells_without_stale_sources(hardware, full_screen):
    term, uart = hardware()
    ref = {"url": "https://example.org/" + ("x" * 130 if full_screen else "")}
    view = display.View(term, "", references=[ref]).install()  # Text cache is blank.
    view.open_link(1)
    chat.show_response(term, "NEW")
    assert not term.view.references
    assert uart.lines()[1].startswith("NEW")
    assert all(not line.strip() for line in uart.lines()[2:19])
    assert_gray_input(uart)
    term.view.references = (ref,)
    term.view.open_link(1)
    term.waiting("WAIT")
    assert all(not line.strip() for line in uart.lines()[1:19])
    assert_gray_input(uart)


def test_malformed_link_stays_local_and_does_not_reuse_prior_sources(hardware):
    keys = submitted("link 999") + submitted("lien potato") + submitted("link") + submitted("next question")
    term, _ = hardware(keys)
    chat.show_response(term, "[1] a fake marker", references=())
    assert chat.read_question(term) == ("next question", "envoi")
    assert term.view.qr is None


def test_main_loop_keeps_source_order_and_never_sends_link_or_test_qr_to_api():
    calls = []
    def answer(history, *args, **kwargs):
        calls.append(history)
        text = "First [1], second [2]."
        return {"status": "ordinary", "text": text,
                "chunks": [{"type": "text", "text": "First "},
                           {"type": "tool_reference", **REFS[0]},
                           {"type": "text", "text": ", second "},
                           {"type": "tool_reference", **REFS[1]}], "references": REFS}
    helper = startup.StartupTests()
    helper.run_session(login_keys("fr") + submitted("first question") + submitted("LINK 2")
                       + fn(chat.K_RETOUR) + submitted("TEST QR") + fn(chat.K_ENVOI)
                       + fn(chat.K_RETOUR) + submitted("second question"), factual_reply=answer)
    assert len(calls) == 2
    assert calls[1][-1]["content"] == "second question"
    assert [turn["content"] for turn in calls[1] if turn["role"] == "user"] == ["first question", "second question"]


def test_replacing_qr_never_repaints_answer_in_between_and_keeps_editor(hardware):
    term, uart = hardware()
    view = display.View(term, "OLD ANSWER", references=REFS).install()
    view.open_link(1)
    assert uart.screen.cursor_visible
    view.buffer = list("next")
    view.input()
    assert view.qr is not None and uart.lines()[21].startswith("> next")
    before = len(uart.output)
    view.open_link(2)
    assert b"OLD ANSWER" not in uart.output[before:]
    assert uart.screen.cursor_visible
    assert view.buffer == list("next")
    assert "Source [2]" in uart.lines()[20]


def test_partial_drawing_failure_restores_g0_echo_and_invalidates_graphics(hardware, monkeypatch):
    term, uart = hardware(submitted("LINK 1"))
    view = display.View(term, "", references=REFS).install()
    routes, original = dict(uart.routes), uart.write
    failed = False
    def write(data):
        nonlocal failed
        if b"\x0e" in data and not failed:
            failed = True
            original(data[:-1])  # Hardware saw a partial graphics row without SI.
            raise RuntimeError("synthetic drawing failure")
        return original(data)
    monkeypatch.setattr(uart, "write", write)
    with pytest.raises(RuntimeError, match="synthetic drawing"):
        view.read()
    assert uart.routes == routes and not uart.screen.mosaic
    assert view.graphics  # Even a partial row must be erased by the next result.
    chat.show_response(term, "RECOVERED")
    assert uart.lines()[1].startswith("RECOVERED")
    assert all(not line.strip() for line in uart.lines()[2:19])
    assert_gray_input(uart)


def test_expired_view_blocks_qr_before_any_graphics_are_written(hardware):
    term, uart = hardware()
    check = Mock()
    view = display.View(term, "Private", references=REFS, check=check).install()
    before = len(uart.output)
    check.side_effect = RuntimeError("expired")
    with pytest.raises(RuntimeError, match="expired"):
        view.open_link(1)
    assert len(uart.output) == before and view.qr is None


def test_full_screen_typeahead_returns_to_input_without_losing_first_character(hardware):
    term, uart = hardware(submitted("LINK 1") + submitted("NEXT"))
    view = display.View(term, long_answer(), references=[{"url": "https://example.org/" + "a" * 130}]).install()
    assert view.read() == ("NEXT", "envoi")
    assert view.qr is None and view.page == 0
    assert uart.screen.cursor_visible
    assert uart.lines()[21].startswith("> NEXT")
    assert_gray_input(uart)


def test_stream_link_numbers_follow_transcript_arrival_not_canonical_order():
    from services.llm_streaming import Conversations
    parser = Conversations([{"role": "user", "content": "Explain sources"}], "fr", True)
    parser.feed("tool.execution.started", {"output_index": 0, "id": "search", "name": "web_search"})
    parser.feed("tool.execution.done", {"output_index": 0, "id": "search", "name": "web_search"})
    for index, content in [(2, {"type": "tool_reference", "tool": "web_search", **REFS[1]}),
                           (0, {"type": "tool_reference", "tool": "web_search", **REFS[0]}),
                           (1, {"type": "text", "text": "First then second."})]:
        parser.feed("message.output.delta", {"output_index": 1, "id": "answer", "content_index": index, "content": content})
    parser.feed("conversation.response.done", {"usage": {}})
    assert [ref["url"] for ref in parser.result["references"]] == [ref["url"] for ref in REFS]
    assert [ref["url"] for ref in parser.result["display_references"]] == [ref["url"] for ref in reversed(REFS)]


@pytest.mark.parametrize("completed", [False, True])
def test_stream_attaches_only_completed_transcript_references(hardware, monkeypatch, completed):
    from services import stream_sources, terminal_scrolling
    term, _ = hardware()
    display.View(term, "Transcript").install()
    monkeypatch.setattr(terminal_scrolling, "show", Mock(return_value="done"))
    # Context ownership is already covered by real producer lifecycle tests.
    class Owned:
        result = {"references": list(reversed(REFS)), "display_references": REFS} if completed else None
        cancel = Mock()
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            pass
    assert stream_sources.show(term, Owned()) == "done"
    assert term.view.references == (tuple(REFS) if completed else ())


@pytest.mark.parametrize("length", [0, 230])
def test_qr_backpressure_presence_echo_and_retour_at_real_wire_speed(paced, length):
    keys = submitted("LINK 1") + fn(chat.K_RETOUR) + submitted("NEXT")
    term, uart, clock = paced(keys, transport=timing_tests.BufferedUART, capacity=256)
    routes = dict(uart.routes)
    chat.show_response(term, long_answer(), references=[{"url": "https://example.org/" + "x" * length}])
    term.view.move(1)
    assert chat.read_question(term) == ("NEXT", "envoi")
    uart.finish()
    assert term.view.page == 1 and term.view.qr is None
    assert not uart.timeouts and uart.waits
    assert uart.routes == routes
    assert all(copies == 0 for _, copies in uart.echo)
    assert not uart.screen.mosaic and uart.screen.cursor_visible
    assert 21 <= uart.screen.row <= 23
    assert clock.now < 120
    assert_gray_input(uart)
