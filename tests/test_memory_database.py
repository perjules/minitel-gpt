"""Real SQLite/legacy-file acceptance; all data synthetic and temporary."""

import json
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest

from services import memory_database as sql
from services import persistent_memory as memory
from services.user_registry import Registry


def legacy(store, key):
    data = {
        "version": 1,
        "revision": 9,
        "language": "en",
        "items": [
            {
                "id": "m_0123456789abcdef",
                "kind": "fact",
                "title": "work",
                "text": "Synthetic process engineer",
                "status": "",
                "updated": memory.now(),
            }
        ],
    }
    raw = json.dumps(data, ensure_ascii=False).encode()
    (store.folder(key) / "memory.json").write_bytes(raw)
    return data, raw


def test_import_preserves_ids_language_bytes_and_is_idempotent(tmp_path):
    store, registry = memory.MemoryStore(tmp_path), Registry(tmp_path)
    alice = registry.resolve("Alice")
    data, raw = legacy(store, alice.memory_key)
    restored = store.read(alice.memory_key)
    assert restored["revision"] == 9 and restored["language"] == "en"
    assert restored["items"][0]["id"] == data["items"][0]["id"]
    assert restored["items"][0]["origin"] == "legacy"
    backup = store.folder(alice.memory_key) / "memory.before-sqlite.json"
    assert backup.read_bytes() == raw and backup.stat().st_mode & 0o777 == 0o600
    assert store.read(alice.memory_key) == restored
    with pytest.raises(memory.MemoryProblem):
        store._read_legacy(store.folder(alice.memory_key))
    with sql.connection(store) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
        assert db.execute("SELECT version FROM memory_schema").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM memory_items").fetchone()[0] == 1


def test_interrupted_marker_write_retries_without_duplicate_import(tmp_path):
    store = memory.MemoryStore(tmp_path)
    user = Registry(tmp_path).resolve("Alice")
    _, raw = legacy(store, user.memory_key)
    with (
        patch.object(
            store, "_write_json", side_effect=OSError("synthetic disk failure")
        ),
        pytest.raises(memory.MemoryProblem),
    ):
        store.read(user.memory_key)
    with sql.connection(store) as db:
        assert db.execute("SELECT count(*) FROM memory_items").fetchone()[0] == 1
    assert (store.folder(user.memory_key) / "memory.json").read_bytes() == raw
    assert store.read(user.memory_key)["revision"] == 9


def test_concurrent_import_and_writes_and_stale_snapshot(tmp_path):
    store = memory.MemoryStore(tmp_path)
    user = Registry(tmp_path).resolve("Alice")
    legacy(store, user.memory_key)
    with ThreadPoolExecutor(max_workers=3) as workers:
        records = list(workers.map(lambda _: store.read(user.memory_key), range(3)))
    assert records[0] == records[1] == records[2]
    with ThreadPoolExecutor(max_workers=4) as workers:
        list(
            workers.map(
                lambda i: store.apply(
                    user.memory_key,
                    {"operation": "save", "kind": "fact", "text": f"Fact {i}"},
                    {},
                ),
                range(8),
            )
        )
    assert len(store.read(user.memory_key)["items"]) == 9
    with pytest.raises(memory.MemoryProblem, match="changé"):
        store.apply(
            user.memory_key,
            {"operation": "remove", "ids": [records[0]["items"][0]["id"]]},
            records[0],
        )


def test_same_legacy_item_ids_remain_owned_and_new_profiles_get_marker(tmp_path):
    store, registry = memory.MemoryStore(tmp_path), Registry(tmp_path)
    users = [registry.resolve(name) for name in ("Alice", "Bob")]
    for user in users:
        legacy(store, user.memory_key)
        store.read(user.memory_key)
    before = store.read(users[1].memory_key)
    data = store.read(users[0].memory_key)
    store.apply(
        users[0].memory_key,
        {"operation": "remove", "ids": [data["items"][0]["id"]]},
        data,
    )
    assert store.read(users[1].memory_key) == before
    default = registry.resolve("")
    store.read(default.memory_key)
    assert (
        json.loads((store.folder(default.memory_key) / "memory.json").read_text())[
            "version"
        ]
        == 2
    )
    with sql.connection(store) as db:
        assert (
            db.execute(
                "SELECT auto_enabled FROM memory_state WHERE user_id=1"
            ).fetchone()[0]
            == 0
        )


def test_unknown_schema_and_malformed_source_never_replaced(tmp_path):
    store = memory.MemoryStore(tmp_path)
    user = Registry(tmp_path).resolve("Alice")
    path = store.folder(user.memory_key) / "memory.json"
    path.write_bytes(b"broken {")
    with pytest.raises(memory.MemoryProblem, match="conservées"):
        store.read(user.memory_key)
    assert path.read_bytes() == b"broken {"
    assert not path.with_name("memory.before-sqlite.json").exists()
    with sql.connection(store) as db:
        db.execute("UPDATE memory_schema SET version=999")
    with pytest.raises(memory.MemoryProblem, match="Version"):
        store.read(user.memory_key)


def test_fifo_legacy_source_is_rejected_without_opening_it(tmp_path):
    import os

    store = memory.MemoryStore(tmp_path)
    user = Registry(tmp_path).resolve("Alice")
    path = store.folder(user.memory_key) / "memory.json"
    os.mkfifo(path)
    with pytest.raises(memory.MemoryProblem, match="non régulier"):
        store.read(user.memory_key)


def test_malformed_manual_sql_edit_is_reported_without_wiping(tmp_path):
    store = memory.MemoryStore(tmp_path)
    user = Registry(tmp_path).resolve("Alice")
    store.apply(
        user.memory_key, {"operation": "save", "kind": "fact", "text": "Good fact."}, {}
    )
    with sql.connection(store) as db:
        db.execute("UPDATE memory_items SET source='not JSON'")
    with pytest.raises(memory.MemoryProblem, match="SQL illisible"):
        store.read(user.memory_key)
    with sql.connection(store) as db:
        assert db.execute("SELECT text FROM memory_items").fetchone()[0] == "Good fact."
