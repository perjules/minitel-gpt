"""Exercise physical-style routing, screen regions and local-only pagination."""

from unittest.mock import Mock

import pytest
import test_terminal_startup as startup
from test_rose_echo import RoutingSerial, submitted
from test_terminal_startup import EndOfInput, fn, login_keys

from services import minitel_chatgpt as chat
from services import terminal_display as display
from services import terminal_documents
from services.console_terminal import Screen


class VisibleUART(RoutingSerial):
    def __init__(self, keys=b"", **kwargs):
        super().__init__(keys, **kwargs)
        self.screen = Screen()

    def write(self, data):
        result = super().write(data)
        self.screen.write(data)
        return result

    def read(self, count):
        before = len(self.echo)
        result = super().read(count)
        if len(self.echo) > before:
            char, copies = self.echo[-1]
            self.screen.write(char.encode() * copies)
        return result

    def lines(self):
        return ["".join(row) for row in self.screen.rows]


@pytest.fixture
def hardware(monkeypatch):
    monkeypatch.setattr(chat.time, "sleep", lambda _: None)
    def make(keys=b"", **kwargs):
        uart = VisibleUART(keys, **kwargs)
        return chat.Term(uart), uart
    return make


def long_answer():
    return "\n".join(f"Result {i:02d}" for i in range(54))


def test_geometry_and_cursor_bounds():
    g = display.STANDARD
    assert (g.columns, g.rows, g.content_first, g.content_rows) == (40, 24, 2, 18)
    assert (g.navigation_row, g.status_row, g.input_first, g.input_rows) == (20, 21, 22, 3)
    assert g.position(24, 40) == bytes.fromhex("1f 58 68")
    for row, column in ((0, 1), (25, 1), (1, 0), (1, 41)):
        with pytest.raises(ValueError):
            g.position(row, column)
    with pytest.raises(ValueError):
        display.Geometry(columns=80)


def assert_gray_input(uart):
    assert all(style == (2, True) for row in uart.screen.styles[21:24] for style in row[:39])
    assert all(style == (7, False) for row in uart.screen.styles[:21] for style in row)
    assert all(row[39] == (7, False) for row in uart.screen.styles[:21])
    assert all(row[39] == (2, True) for row in uart.screen.styles[21:])


@pytest.mark.parametrize("title", ["3615 IA", "3615 IA / COURRIER", "A" * 80])
def test_heading_is_centered_between_stars_and_input_is_solid_inverse(hardware, title):
    term, uart = hardware()
    display.View(term, "Body", title=title).install()
    heading = uart.lines()[0][:39]
    assert heading.strip().startswith("*** ") and heading.strip().endswith(" ***")
    assert abs(len(heading) - len(heading.lstrip()) - (len(heading) - len(heading.rstrip()))) <= 1
    assert len(heading.strip()) <= 39
    assert_gray_input(uart)
    assert uart.lines()[21].strip() == ">"
    assert all(not line.strip() for line in uart.lines()[22:])


def test_inverse_input_survives_editing_viewport_and_can_submission_wipe(hardware, monkeypatch):
    term, uart = hardware()
    chat.show_response(term, "Small answer")
    for value in ("HELLO", "HELL", "", "Q" * 113, "Q" * 114, "Q" * 115, "Q" * 100):
        term.view.buffer = list(value)
        term.view.input()
        assert_gray_input(uart)

    original = uart.write
    styles = []
    def observe(data):
        written = original(data)
        styles.append(all(style == (2, True) for row in uart.screen.styles[21:24] for style in row[:39]))
        return written
    monkeypatch.setattr(uart, "write", observe)
    term.waiting("WAIT")
    assert styles and all(styles)  # Erased characters must not leave bright or black holes in the gray field.
    assert_gray_input(uart)
    assert uart.lines()[21].strip() == ">"


def test_can_paging_preserves_inverse_input_and_centred_heading_with_draft(hardware):
    term, uart = hardware(b"DRAFT" + fn(chat.K_SUITE) + fn(chat.K_RETOUR) + submitted(" END"))
    chat.show_response(term, long_answer())
    assert chat.read_question(term) == ("DRAFT END", "envoi")
    assert uart.output.count(b"\x0c") == 1
    assert uart.lines()[0][:39] == "*** 3615 IA ***".center(39)
    assert_gray_input(uart)
    assert uart.lines()[21].startswith("> DRAFT END")
    term.waiting("WAIT")  # Dense content clears rows while retaining the gray field.
    assert_gray_input(uart)


def test_input_color_change_repaints_identical_text_without_dimming_other_regions(hardware):
    term, uart = hardware()
    chat.show_response(term, "Bright body text")
    term.view.row(22, "> ", inverse=True, foreground=7)
    assert uart.screen.styles[21][0] == (7, True)
    before = len(uart.output)
    term.view.input()
    assert b"\x1b\x42" in uart.output[before:]  # Select green/70% before writing inverse spaces.
    assert_gray_input(uart)
    assert uart.lines()[1].startswith("Bright body text")


@pytest.mark.parametrize("length", [36, 37, 38, 76, 77, 113, 114, 115, 1999, 2000])
def test_input_wrap_and_viewport_never_touch_content_or_roll_screen(hardware, length):
    term, uart = hardware(submitted("A" * length))
    chat.show_response(term, "TOP\n" + "\n".join(f"line{i}" for i in range(17)))
    content = uart.lines()[1:19]
    routes = dict(uart.routes)
    assert chat.read_question(term) == ("A" * length, "envoi")
    assert uart.lines()[1:19] == content
    assert "3615 IA" in uart.lines()[0]
    assert "Page 1/1" in uart.lines()[19]
    assert 21 <= uart.screen.row <= 23
    assert all(line[-1] == " " for line in uart.lines())
    assert all(copies == 0 for _, copies in uart.echo)
    assert uart.routes == routes
    assert len(uart.output) < length * 24 + 1500  # No line/page repaint per key.


def test_dense_paging_preserves_input_and_only_clears_on_real_page_changes(hardware):
    keys = b"RE" + fn(chat.K_SUITE) * 9 + fn(chat.K_RETOUR) * 2 + submitted("PONDRE oui")
    term, uart = hardware(keys)
    chat.show_response(term, long_answer())
    clears = uart.output.count(b"\x0c")
    assert chat.read_question(term) == ("REPONDRE oui", "envoi")
    assert term.view.page == 0
    assert uart.output.count(b"\x0c") == clears
    assert uart.lines()[1].startswith("Result 00")
    assert uart.lines()[18].startswith("Result 17")
    assert uart.lines()[21].startswith("> REPONDRE oui")
    assert uart.lines()[0][:39] == "*** 3615 IA ***".center(39)
    assert all(not inverse for _, inverse in uart.screen.styles[0])
    assert uart.screen.styles[1][0][1] is False


def test_correction_and_annulation_at_viewport_boundary_keep_full_command(hardware):
    keys = b"B" * 114 + fn(chat.K_CORR) + b"C" + fn(chat.K_ANNUL) + submitted("MAIL")
    term, uart = hardware(keys)
    chat.show_response(term, "Answer preserved")
    assert chat.read_question(term) == ("MAIL", "envoi")
    assert uart.lines()[1].startswith("Answer preserved")
    assert uart.lines()[21].startswith("> MAIL")
    assert not any("BBBB" in row for row in uart.lines()[21:])


def test_oversized_command_is_drained_not_submitted_as_prefix_or_suffix(hardware):
    term, _ = hardware(submitted("A" * 2001) + submitted("NEXT COMMAND"))
    chat.show_response(term, "Answer")
    assert chat.read_question(term) == ("NEXT COMMAND", "envoi")


@pytest.mark.parametrize("key,action", [(chat.K_SOMMAIRE, "sommaire"), (chat.K_GUIDE, "guide")])
def test_navigation_exit_restores_exact_routes(hardware, key, action):
    term, uart = hardware(b"unfinished" + fn(key), direct=True, modem=False)
    before = dict(uart.routes)
    chat.show_response(term, long_answer())
    assert chat.read_question(term) == (None, action)
    assert uart.routes == before
    assert all(copies == 0 for _, copies in uart.echo)


def test_timeout_and_link_failure_restore_echo(hardware, monkeypatch):
    term, uart = hardware()
    before = dict(uart.routes)
    chat.show_response(term, "Answer")
    monkeypatch.setattr(term, "read_key", Mock(return_value=("timeout", None)))
    assert chat.read_question(term) == (None, "timeout")
    assert uart.routes == before
    monkeypatch.setattr(term, "read_key", Mock(side_effect=EndOfInput))
    with pytest.raises(EndOfInput):
        chat.read_question(term)
    assert uart.routes == before


def test_next_result_clears_dense_page_and_erases_submitted_input(hardware):
    term, uart = hardware(submitted("MAIL"))
    chat.show_response(term, long_answer())
    assert chat.read_question(term) == ("MAIL", "envoi")
    chat.show_response(term, "Short")
    assert uart.output.count(b"\x0c") == 1
    assert uart.lines()[1].startswith("Short")
    assert all(not line.strip() for line in uart.lines()[2:19])
    assert uart.lines()[21].strip() == ">"


def test_textual_paging_commands_never_return_to_feature_or_model(hardware):
    term, uart = hardware(submitted("SUIVANT") + submitted("PRECEDENT") + submitted("MAIL"))
    chat.show_response(term, long_answer())
    assert chat.read_question(term) == ("MAIL", "envoi")
    assert term.view.page == 0
    assert uart.lines()[1].startswith("Result 00")


def test_review_is_not_granted_by_visiting_only_the_last_page(hardware):
    term, uart = hardware(submitted("ENVOYER"))
    reviewed = Mock()
    view = display.View(term, long_answer(), reviewed=reviewed).install()
    view.page = 2
    view.draw()
    assert view.read() == ("ENVOYER", "envoi")
    reviewed.assert_not_called()  # Page 2 was never displayed.
    assert uart.routes[0x5A] & 2


def test_expired_private_view_does_not_acknowledge_or_accept_command(hardware):
    term, uart = hardware(submitted("ENVOYER"))
    check, reviewed = Mock(), Mock()
    view = display.View(term, long_answer(), check=check, reviewed=reviewed).install()
    check.side_effect = RuntimeError("expired")
    with pytest.raises(RuntimeError, match="expired"):
        view.read(echo=True)
    reviewed.assert_not_called()
    assert bytes(uart.keys) == submitted("ENVOYER")


def test_ordinary_chat_accepts_follow_up_on_first_page_without_refetching():
    calls = []
    def factual(history, *args, **kwargs):
        calls.append(history)
        text = long_answer() if len(calls) == 1 else "Second answer"
        return {"status": "ordinary", "text": text,
                "chunks": [{"type": "text", "text": text}], "references": []}
    helper = startup.StartupTests()
    helper.run_session(login_keys("fr") + submitted("Question one") + submitted("Question two"),
                       factual_reply=factual)
    assert len(calls) == 2
    assert calls[1][-1] == {"role": "user", "content": "Question two"}
    assert calls[1][1]["content"] == long_answer()


def test_journal_reader_does_not_override_outer_hidden_echo(hardware):
    term, uart = hardware(submitted("T: note"), modem=False)
    before = dict(uart.routes)
    assert terminal_documents.read(term, "Private document", echo=True) == ("T: note", "envoi")
    assert uart.routes == before
    assert uart.commands == []


def test_grade_navigation_keeps_draft_while_preserving_feature_key_meaning(hardware):
    term, uart = hardware(b"LIRE " + fn(chat.K_SUITE) + submitted("2"))
    chat.show_response(term, "Grade ALPHA")
    assert chat.read_question(term, navigation_mode=True) == ("SUIVANT", "envoi")
    chat.show_response(term, "Grade BETA")
    assert chat.read_question(term, navigation_mode=True) == ("LIRE 2", "envoi")
    assert uart.lines()[21].startswith("> LIRE 2")


def test_session_reset_retires_loaded_content_and_partial_input():
    helper = startup.StartupTests()
    helper.run_session(login_keys("fr") + submitted("FIRST") + b"unsent" + fn(chat.K_SOMMAIRE))
    assert not any("unsent" in row for row in helper.result_screens[-1].splitlines())
    assert "17 x 23" not in helper.result_screens[-1]
    assert "Quelle est votre requete" in helper.result_screens[-1]


def test_multiline_status_cannot_move_cursor_or_overwrite_other_regions(hardware):
    term, uart = hardware()
    chat.show_response(term, "Preserved answer")
    term.view.status("ONE\nTWO\tTHREE")
    assert uart.lines()[20].startswith("ONE TWO THREE")
    assert uart.lines()[1].startswith("Preserved answer")


def test_document_echo_failure_exits_cleanly_without_accepting_input(hardware, monkeypatch):
    from contextlib import contextmanager
    term, uart = hardware(submitted("ENVOYER"))
    @contextmanager
    def unavailable(_term):
        raise display.rose_terminal.EchoUnavailable("fixture")
        yield  # pragma: no cover
    monkeypatch.setattr(display.rose_terminal, "hidden_echo", unavailable)
    assert terminal_documents.read(term, long_answer()) == (None, "sommaire")
    assert term.view is None
    assert bytes(uart.keys) == submitted("ENVOYER")


def test_update_confirmation_waits_for_complete_loaded_preview(hardware, monkeypatch):
    from queue import Queue

    from services import deployment
    term, _ = hardware(submitted("OUI") + fn(chat.K_SUITE) * 2 + submitted("OUI"))
    calls = []
    def request(language, preview=None):
        calls.append(preview)
        if preview is None:
            result = {"display": long_answer(), "preview": {"ok": True, "behind": 1}}
        else:
            assert term.view.seen == {0, 1, 2}
            result = {"rows": ["Procedure"]}
        output = Queue()
        output.put((result, None))
        return Mock(is_alive=lambda: False), output, chat.time.monotonic()
    monkeypatch.setattr(chat, "start_update_request", request)
    monkeypatch.setattr(deployment, "unchanged", lambda *args: True)
    monkeypatch.setattr(deployment, "queue_deployment", Mock(return_value=(False, "test refusal")))
    monkeypatch.setattr(chat.mainframe_update, "arm_resume", Mock())
    monkeypatch.setattr(chat.mainframe_update, "clear_resume", Mock())
    assert chat.terminal_update(term, "fr")[0] == "done"
    assert calls == [None, {"ok": True, "behind": 1}]


def test_waiting_message_is_centered_in_status_and_caret_returns_to_input(hardware):
    term, uart = hardware(submitted("NEXT"))
    chat.show_response(term, "Loaded content remains here")
    term.view.buffer = list("previous command")
    term.view.input()
    before = len(uart.output)
    term.waiting("CONSULTATION PRONOTE")
    assert all(not line.strip() for line in uart.lines()[1:19])
    assert uart.lines()[20][:39] == "CONSULTATION PRONOTE".center(39)
    assert uart.lines()[21].strip() == ">"
    assert all(not row.strip() for row in uart.lines()[22:])
    assert not uart.screen.cursor_visible
    assert 21 <= uart.screen.row <= 23
    assert b"\r\n" not in uart.output[before:]
    chat.show_response(term, "New answer")
    assert chat.read_question(term) == ("NEXT", "envoi")
    assert uart.screen.cursor_visible
    assert 21 <= uart.screen.row <= 23


def test_long_waiting_message_stays_on_one_status_row(hardware):
    term, uart = hardware()
    chat.show_response(term, "KEEP")
    term.waiting("A very long waiting message\n" * 5)
    assert uart.lines()[20][:39].endswith("...")
    assert not uart.lines()[1].strip()
    assert uart.lines()[21].strip() == ">"


def test_submission_clears_input_then_status_then_content_bottom_to_top(hardware):
    term, uart = hardware(submitted("Q" * 100))
    old = "Repeated prefix OLD\n" + "x\n" * 16 + "y"
    chat.show_response(term, old)
    assert chat.read_question(term) == ("Q" * 100, "envoi")
    before = len(uart.output)
    screen = Screen()
    screen.write(uart.output)
    header = list(screen.rows[0])

    term.waiting("ATTENTE...")
    phases, input_cells, answer_cells = [], [], []
    for code in uart.output[before:]:
        previous = [list(row) for row in screen.rows]
        screen.write(bytes([code]))
        assert screen.rows[0] == header
        assert all(row[-1] == " " for row in screen.rows)
        for row in range(24):
            for column in range(39):
                if screen.rows[row][column] == previous[row][column]:
                    continue
                assert not screen.cursor_visible
                if row >= 21:
                    phases.append(0)
                    input_cells.append((row, column))
                    assert screen.rows[row][column] == " "
                elif row in (19, 20):
                    phases.append(1)
                else:
                    phases.append(2)
                    answer_cells.append((row, column))
                    assert screen.rows[row][column] == " "
                    assert "".join(screen.rows[20][:39]) == "ATTENTE...".center(39)
                    assert "".join(screen.rows[21]).strip() == ">"
                    assert not any("".join(line).strip() for line in screen.rows[22:])
    assert phases == sorted(phases) and set(phases) == {0, 1, 2}
    assert list(dict.fromkeys(row for row, _ in input_cells)) == [23, 22, 21]
    assert b"\x08 \x08" not in uart.output[before:]
    assert list(dict.fromkeys(row for row, _ in answer_cells)) == list(range(18, 0, -1))
    assert not any("".join(row).strip() for row in screen.rows[1:19])
    assert b"\x0c" not in uart.output[before:]

    before = len(uart.output)
    chat.show_response(term, "Repeated prefix NEW")
    assert b"Repeated prefix NEW" in uart.output[before:]
    for code in uart.output[before:]:
        previous = [list(row) for row in screen.rows]
        screen.write(bytes([code]))
        for row in range(1, 19):
            for column in range(39):
                if previous[row][column] != screen.rows[row][column]:
                    assert previous[row][column] == " "
    assert uart.lines()[1].startswith("Repeated prefix NEW")
    assert all(not line.strip() for line in uart.lines()[2:19])
    assert uart.output.count(b"\x0c") == 1


def test_uncertain_cache_recovers_with_full_clear_and_restores_frame(hardware):
    term, uart = hardware()
    chat.show_response(term, long_answer())
    for row in (1, 18, 21, 23):
        uart.screen.rows[row][38] = "X"
    term.view.cache.clear()  # Simulate lost knowledge of the physical screen.
    term.waiting("WAIT")
    assert uart.output.count(b"\x0c") == 2
    assert all(not line.strip() for line in uart.lines()[1:19])
    assert uart.lines()[0][:39] == "*** 3615 IA ***".center(39)
    assert uart.lines()[19].startswith("Requete en cours")
    assert uart.lines()[20][:39] == "WAIT".center(39)
    assert uart.lines()[21].strip() == ">"
    assert all(not line.strip() for line in uart.lines()[22:])


@pytest.mark.parametrize("visible,clears", [(100, 0), (101, 0), (700, 0)])
def test_can_transition_retains_frame_at_every_content_density(hardware, visible, clears):
    term, uart = hardware()
    chat.show_response(term, "X" * visible)
    before = len(uart.output)
    chat.show_response(term, "Short")
    assert uart.output[before:].count(b"\x0c") == clears
    assert uart.lines()[1].startswith("Short")
    assert all(not line.strip() for line in uart.lines()[2:19])
    assert uart.lines()[0][:39] == "*** 3615 IA ***".center(39)
    assert "Page 1/1" in uart.lines()[19]


def test_sparse_first_page_of_large_document_uses_can(hardware):
    term, uart = hardware()
    chat.show_response(term, "FIRST" + "\n" * 18 + "Z" * 800)
    before = len(uart.output)
    term.view.move(1)
    assert b"\x0c" not in uart.output[before:]
    assert uart.lines()[1].startswith("Z")
    before = len(uart.output)
    term.view.move(-1)
    assert uart.output[before:].count(b"\x0c") == 0
    assert uart.lines()[1].startswith("FIRST")


def test_can_paging_preserves_long_draft_and_its_viewport(hardware):
    keys = b"Q" * 200 + fn(chat.K_SUITE) + fn(chat.K_RETOUR) + submitted("Z")
    term, uart = hardware(keys)
    chat.show_response(term, long_answer())
    assert chat.read_question(term) == ("Q" * 200 + "Z", "envoi")
    assert uart.output.count(b"\x0c") == 1
    assert uart.lines()[21].startswith("> " + "Q" * 10)
    assert "Saisie 201/2000" in uart.lines()[20]
    assert 21 <= uart.screen.row <= 23


@pytest.mark.parametrize("title", ["3615 IA / COURRIER", "3615 IA / PRONOTE", "3615 IA / JOURNAL"])
def test_local_document_replacement_uses_clear_transition_without_waiting(hardware, title):
    term, uart = hardware(submitted("LIRE 1") + submitted("SOMETHING"))
    reviewed = Mock()
    assert terminal_documents.read(term, long_answer(), title=title, reviewed=reviewed) == ("LIRE 1", "envoi")
    reviewed.assert_not_called()
    before = len(uart.output)
    assert terminal_documents.read(term, "Short document", title=title) == ("SOMETHING", "envoi")
    assert uart.output[before:].count(b"\x0c") == 0
    assert uart.lines()[0][:39] == f"*** {title} ***".center(39)
    assert uart.lines()[1].startswith("Short document")
    assert all(not line.strip() for line in uart.lines()[2:19])
