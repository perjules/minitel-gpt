"""Waiting theatre respects the actual request lifecycle and UART frame."""
from itertools import islice, pairwise
from unittest.mock import Mock

from test_terminal_display import VisibleUART

from services import minitel_chatgpt as chat
from services import terminal_display, waiting_tasks


def test_curated_tasks_fit_status_and_do_not_repeat_within_bag():
    assert len(set(waiting_tasks.TASKS)) == len(waiting_tasks.TASKS)
    assert all(len(task) <= 34 and task.isascii() for task in waiting_tasks.TASKS)
    items = list(islice(waiting_tasks.sequence(), len(waiting_tasks.TASKS) * 2))
    assert len(set(items[:len(waiting_tasks.TASKS)])) == len(waiting_tasks.TASKS)
    assert all(a != b for a, b in pairwise(items))


def test_waiting_tasks_rotate_without_clearing_content_or_input(monkeypatch):
    monkeypatch.setattr(chat.time, 'sleep', lambda _: None)
    clock = [0.0]
    monkeypatch.setattr(chat.time, 'monotonic', lambda: clock[0])
    term = chat.Term(VisibleUART())
    view = terminal_display.View(term, 'Preserved content').install()
    view.buffer = list('draft')
    view.input()
    before = term.s.lines()
    queue = chat.Queue()
    def read(timeout):
        clock[0] += .5
        if clock[0] == 4.5:
            queue.put(({'display': 'Done', 'turn': None}, None))
        return 'timeout', None
    term.read_key = read
    status = Mock(wraps=view.status)
    monkeypatch.setattr(view, 'status', status)
    result, action = chat.wait_answer(term, (Mock(), queue, 0), 20,
                                      tasks=iter(['Task one', 'Task two', 'Task three']))
    assert action == 'done' and result['display'] == 'Done'
    assert [call.args[0] for call in status.call_args_list] == ['Task one', 'Task two']
    assert clock[0] == 4.5
    assert view.buffer == list('draft')
    assert term.s.lines()[:20] == before[:20]
    assert term.s.lines()[21:] == before[21:]


def test_ready_result_and_cancellation_do_not_add_tasks(monkeypatch):
    monkeypatch.setattr(chat.time, 'monotonic', lambda: 0)
    queue = chat.Queue()
    queue.put(({'display': 'Done'}, None))
    term = Mock()
    tasks = Mock()
    assert chat.wait_answer(term, (Mock(), queue, 0), 20, tasks=tasks)[1] == 'done'
    term.read_key.assert_not_called()
    term.view.status.assert_not_called()
    term.read_key.return_value = ('fn', chat.K_SOMMAIRE)
    assert chat.wait_answer(term, (Mock(), queue, 0), 20, tasks=tasks)[1] == 'sommaire'
    term.view.status.assert_not_called()
