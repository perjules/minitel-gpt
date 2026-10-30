"""Bounded automatic learning with synthetic grounded statements and mocked APIs."""

import json
import time
from unittest.mock import Mock

import pytest

from services import memory_automation as auto
from services import memory_database as sql
from services import memory_session as sessions
from services import memory_vectors as vectors
from services import persistent_memory as memory


@pytest.fixture
def subject(tmp_path):
    selected = sessions.MemorySession("Alice", root=tmp_path)
    selected.model = Mock()
    selected.embeddings = Mock()
    selected.embeddings.embed.return_value = [[1.0] + [0.0] * 1023] * 8
    yield selected
    selected.close()


def statements(selected, count=6):
    for i in range(count):
        selected.record("user", f"I work on synthetic reactor number {i}.")


def queue(selected):
    with sql.connection(selected.store) as db:
        return [
            dict(r)
            for r in db.execute(
                "SELECT * FROM memory_events WHERE user_id=? ORDER BY id",
                (selected.user.id,),
            )
        ]


def result(selected, events=None, **changes):
    event = (events or queue(selected))[0]
    proposal = {
        "operation": "save",
        "kind": "fact",
        "title": "work",
        "text": "Works on a synthetic reactor.",
        "id": None,
        "evidence": [{"event_id": event["id"], "quote": event["text"]}],
    }
    proposal.update(changes)
    return json.dumps({"memories": [proposal]})


def test_periodic_batch_provenance_once_only_and_vector_indexing(subject):
    statements(subject, 5)
    assert auto.claim(subject.store) is None
    statements(subject, 1)
    subject.model.client.complete.return_value = result(subject)
    subject.embeddings.embed.return_value = [[1.0] + [0.0] * 1023]
    assert auto.run_once(subject.store, subject.model, subject.embeddings) == (True, 1)
    records = subject.store.read(subject.key)["items"]
    assert len(records) == 1 and records[0]["origin"] == "automatic"
    assert records[0]["source"][0]["session_id"] == subject.session_id
    assert not queue(subject)
    assert auto.run_once(subject.store, subject.model, subject.embeddings) == (False, 0)
    subject.model.client.complete.assert_called_once()


def test_close_and_restart_make_short_batches_ready(subject):
    statements(subject, 1)
    assert auto.claim(subject.store) is None
    subject.close()
    assert auto.claim(subject.store) is not None
    with sql.connection(subject.store) as db:
        db.execute("UPDATE memory_state SET lease_until=0,ready=0")
    restored = sessions.MemorySession("Alice", root=subject.store.root)
    try:
        assert auto.claim(restored.store) is not None
    finally:
        restored.close()


def test_explicit_forget_invalidates_queued_and_inflight_extraction(subject):
    item, _ = subject.store.apply(
        subject.key, {"operation": "save", "kind": "fact", "text": "Old fact"}, {}
    )
    statements(subject)
    proposal = result(subject)

    def late(*args, **kwargs):
        snapshot = subject.store.read(subject.key)
        subject.store.apply(
            subject.key, {"operation": "remove", "ids": [item["id"]]}, snapshot
        )
        return proposal

    subject.model.client.complete.side_effect = late
    assert not auto.process(subject.store, subject.model, subject.embeddings)
    assert not subject.store.read(subject.key)["items"]
    assert not queue(subject)


@pytest.mark.parametrize(
    "change",
    [
        {"user_id": 999},
        {"kind": "task"},
        {"operation": "remove"},
        {"id": "m_0123456789abcdef", "operation": "replace"},
        {"evidence": [{"event_id": 99999, "quote": "Invented source"}]},
        {"evidence": [{"event_id": 1, "quote": "Invented source"}]},
        {"text": "password: synthetic-secret"},
    ],
)
def test_invalid_batches_never_write_and_retry_is_deferred(subject, change):
    statements(subject)
    subject.model.client.complete.return_value = result(subject, **change)
    assert not auto.process(subject.store, subject.model, subject.embeddings)
    assert not subject.store.read(subject.key)["items"]
    assert len(queue(subject)) == 6
    assert auto.claim(subject.store) is None
    with sql.connection(subject.store) as db:
        state = db.execute(
            "SELECT * FROM memory_state WHERE user_id=?", (subject.user.id,)
        ).fetchone()
        assert state["last_status"] == "retry" and state["retry_after"] > time.time()


def test_database_lease_blocks_second_process_and_expired_lease_recovers(subject):
    statements(subject)
    first = auto.claim(subject.store)
    assert first and auto.claim(subject.store) is None
    with sql.connection(subject.store) as db:
        db.execute("UPDATE memory_state SET lease_until=0")
    second = auto.claim(subject.store)
    assert second and second["token"] != first["token"]


def test_default_and_excluded_channels_secrets_and_global_pause(subject, monkeypatch):
    for channel in ("gmail", "memory", "theatre", "session"):
        subject.record("user", "I work at a synthetic factory.", channel)
    subject.record("assistant", "The user is an invented surgeon.")
    subject.record("user", "password: do-not-store-this")
    subject.record("user", "RETIENS une préférence.")
    assert not queue(subject)
    default = sessions.MemorySession("", root=subject.store.root)
    try:
        statements(default)
        assert not queue(default)
    finally:
        default.close()
    monkeypatch.setenv("MINITEL_MEMORY_AUTO", "0")
    statements(subject)
    assert not queue(subject)


def test_disabled_profile_and_changed_profile_during_extraction_cannot_commit(subject):
    statements(subject)
    response = result(subject)

    def disable(*args, **kwargs):
        with subject.registry.connection() as db:
            db.execute(
                "UPDATE users SET enabled=0,revision=revision+1 WHERE id=?",
                (subject.user.id,),
            )
        return response

    subject.model.client.complete.side_effect = disable
    assert not auto.process(subject.store, subject.model, subject.embeddings)
    assert not subject.store.read(subject.key)["items"]
    assert auto.claim(subject.store) is None


def test_queue_is_bounded_and_empty_extraction_is_valid(subject):
    statements(subject, auto.MAX_PENDING + 8)
    assert len(queue(subject)) == auto.MAX_PENDING
    subject.model.client.complete.return_value = '{"memories":[]}'
    assert auto.process(subject.store, subject.model, subject.embeddings)
    assert len(queue(subject)) == auto.MAX_PENDING - auto.BATCH_SIZE
    assert not subject.store.read(subject.key)["items"]


def test_replacing_a_note_requires_explicit_operation(subject):
    item, _ = subject.store.apply(
        subject.key,
        {
            "operation": "save",
            "kind": "note",
            "title": "recipe",
            "text": "Full recipe.",
        },
        {},
    )
    statements(subject)
    subject.model.client.complete.return_value = result(
        subject, operation="replace", id=item["id"]
    )
    assert not auto.process(subject.store, subject.model, subject.embeddings)
    assert subject.store.read(subject.key)["items"][0]["text"] == "Full recipe."


def test_index_failure_keeps_saved_memory_and_uses_backoff(subject, monkeypatch):
    monkeypatch.setattr(auto, "_next_index_attempt", 0)
    subject.store.apply(
        subject.key, {"operation": "save", "kind": "fact", "text": "Local fact."}, {}
    )
    subject.embeddings.embed.side_effect = memory.MemoryProblem("synthetic outage")
    assert auto.run_once(subject.store, subject.model, subject.embeddings) == (False, 0)
    assert subject.store.read(subject.key)["items"][0]["text"] == "Local fact."
    assert auto._next_index_attempt > time.monotonic()
    auto.run_once(subject.store, subject.model, subject.embeddings)
    subject.embeddings.embed.assert_called_once()


def test_recent_mail_never_becomes_an_embedding_query(subject, monkeypatch):
    subject.store.apply(
        subject.key, {"operation": "save", "kind": "fact", "text": "Local fact."}, {}
    )
    subject.embeddings.embed.return_value = [[1.0] + [0.0] * 1023]
    vectors.index_pending(subject.store, subject.embeddings)
    subject.record("assistant", "PRIVATE MAIL BODY", "gmail")
    subject.context("What do I do?")
    assert "PRIVATE MAIL BODY" not in str(subject.embeddings.embed.call_args)


def test_dispatch_is_nonblocking_and_only_one_finite_worker(
    subject, monkeypatch, no_memory_background_network
):
    import threading

    entered, release = threading.Event(), threading.Event()
    actual_thread = threading.Thread
    workers = []

    def thread(*args, **kwargs):
        worker = actual_thread(*args, **kwargs)
        workers.append(worker)
        return worker

    def once(*args):
        entered.set()
        assert release.wait(2)
        return True, 0

    subject.model.client.profile.return_value = {"key": "synthetic-key"}
    monkeypatch.setattr(auto.threading, "Thread", thread)
    bounded = Mock(side_effect=once)
    monkeypatch.setattr(auto, "run_once", bounded)
    try:
        no_memory_background_network(subject.store, subject.model, subject.embeddings)
        assert entered.wait(1)
        no_memory_background_network(subject.store, subject.model, subject.embeddings)
        assert len(workers) == 1
    finally:
        release.set()
        for worker in workers:
            worker.join(2)
    assert bounded.call_count == 2 and not auto._worker.locked()


def test_explicit_override_releases_old_lease_and_keeps_event_ids_unique(subject):
    statements(subject)
    old_ids = [event["id"] for event in queue(subject)]
    assert auto.claim(subject.store)
    subject.store.apply(
        subject.key,
        {"operation": "save", "kind": "preference", "text": "Short replies."},
        {},
    )
    statements(subject)
    assert min(event["id"] for event in queue(subject)) > max(old_ids)
    assert auto.claim(subject.store) is not None
    assert sessions.explicit_request("FORGET my old address")


def test_old_answer_finishing_after_forget_cannot_enqueue_earlier_statement(subject):
    item, _ = subject.store.apply(
        subject.key, {"operation": "save", "kind": "fact", "text": "Old address."}, {}
    )
    subject.begin_turn()  # The ordinary request has started; its model call is still running.
    snapshot = subject.store.read(subject.key)
    subject.store.apply(
        subject.key, {"operation": "remove", "ids": [item["id"]]}, snapshot
    )
    subject.record("user", "I live at the old address.")
    assert not queue(subject)
    assert not subject.store.read(subject.key)["items"]
    subject.begin_turn()
    subject.record("user", "I now work at a synthetic laboratory.")
    assert len(queue(subject)) == 1
