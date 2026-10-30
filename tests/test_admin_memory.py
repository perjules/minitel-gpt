"""Existing admin authentication, additional memory gate, real SQL and exports."""

import json
import time
from unittest.mock import Mock

from services import admin_ui as admin
from services import memory_database as sql
from services import memory_session as sessions


def populate():
    profiles = []
    for name in ("Alice", "Bob"):
        selected = sessions.MemorySession(name, model=Mock())
        selected.store.apply(
            selected.key,
            {
                "operation": "save",
                "kind": "fact",
                "title": "<script>alert(1)</script>",
                "text": f"PRIVATE MEMORY {name}",
            },
            {},
        )
        selected.record("assistant", f"TRANSCRIPT ONLY {name}", "gmail")
        profiles.append(selected)
    for p in profiles:
        p.close()
    return profiles


def unlock(client, headers):
    return client.post(
        "/maintenance/memory",
        data={"action": "unlock", "password": "synthetic-admin-password"},
        headers=headers,
    )


def test_inventory_has_no_content_until_gate_and_gate_is_session_bound(console):
    client, headers, _ = console
    users = populate()
    response = client.get("/maintenance/memory")
    assert response.status_code == 200
    assert "Alice" in response.text and "Bob" in response.text
    assert (
        "PRIVATE MEMORY" not in response.text and "TRANSCRIPT ONLY" not in response.text
    )
    for path in (
        f"/maintenance/memory/{users[0].user.id}",
        "/maintenance/memory/export",
        f"/maintenance/memory/export/{users[0].user.id}",
    ):
        assert client.get(path).status_code == 403
    assert (
        client.post(
            "/maintenance/memory",
            data={"action": "unlock", "password": "wrong"},
            headers=headers,
        ).status_code
        == 401
    )
    assert unlock(client, headers).status_code == 302
    response = client.get(f"/maintenance/memory/{users[0].user.id}")
    assert response.status_code == 200 and "PRIVATE MEMORY Alice" in response.text
    assert "PRIVATE MEMORY Bob" not in response.text
    assert (
        "<script>alert(1)</script>" not in response.text
        and "&lt;script&gt;" in response.text
    )
    assert "no-store" in response.headers["Cache-Control"]
    assert response.headers["Referrer-Policy"] == "no-referrer"
    other = admin.app.test_client()
    with other.session_transaction() as cookie:
        cookie["logged_in"] = True
    assert other.get("/maintenance/memory/export").status_code == 403
    with client.session_transaction() as cookie:
        assert "PRIVATE" not in str(
            dict(cookie)
        ) and "synthetic-admin-password" not in str(dict(cookie))


def test_individual_and_all_export_are_json_without_vectors_or_logs(console):
    client, headers, _ = console
    users = populate()
    unlock(client, headers)
    response = client.get(f"/maintenance/memory/export/{users[0].user.id}")
    assert (
        response.mimetype == "application/json"
        and "attachment" in response.headers["Content-Disposition"]
    )
    data = response.json
    assert data["complete"] and len(data["profiles"]) == 1
    assert (
        "PRIVATE MEMORY Alice" in response.text
        and "PRIVATE MEMORY Bob" not in response.text
    )
    assert "TRANSCRIPT ONLY" not in response.text and "embedding" not in response.text
    response = client.get("/maintenance/memory/export")
    assert response.json["complete"] and len(response.json["profiles"]) == 3
    assert (
        "PRIVATE MEMORY Alice" in response.text
        and "PRIVATE MEMORY Bob" in response.text
    )
    assert client.get("/maintenance/memory/export/999999").status_code == 404
    assert client.get("/maintenance/memory/999999").status_code == 404


def test_csrf_expiry_relock_logout_and_no_auth(console):
    client, headers, _ = console
    populate()
    assert (
        client.post(
            "/maintenance/memory",
            data={"action": "unlock", "password": "synthetic-admin-password"},
        ).status_code
        == 403
    )
    unlock(client, headers)
    with client.session_transaction() as cookie:
        cookie["memory_unlock_until"] = time.time() - 1
    assert client.get("/maintenance/memory/export").status_code == 403
    unlock(client, headers)
    assert (
        client.post(
            "/maintenance/memory", data={"action": "lock"}, headers=headers
        ).status_code
        == 302
    )
    assert client.get("/maintenance/memory/export").status_code == 403
    unlock(client, headers)
    client.post("/logout", headers=headers)
    assert client.get("/maintenance/memory").status_code == 302
    assert client.get("/maintenance/memory/export").status_code == 302


def test_disabled_profiles_remain_inspectable_and_bad_legacy_sources_are_reported(
    console,
):
    client, headers, _ = console
    users = populate()
    with users[0].registry.connection() as db:
        db.execute("UPDATE users SET enabled=0 WHERE id=?", (users[0].user.id,))
    # Unknown historical folder is preserved and included in the global export manifest.
    folder = users[0].store.folder("users/historical-0123456789abcdef")
    (folder / "memory.json").write_text("broken {")
    assert "historical" in client.get("/maintenance/memory").text
    unlock(client, headers)
    response = client.get(f"/maintenance/memory/{users[0].user.id}")
    assert response.status_code == 200 and "PRIVATE MEMORY Alice" in response.text
    data = client.get("/maintenance/memory/export").json
    assert not data["complete"] and data["unassigned_legacy"][0]["error"]
    assert (folder / "memory.json").read_text() == "broken {"
    with sql.connection(users[0].store) as db:
        assert db.execute("SELECT count(*) FROM memory_items").fetchone()[0] == 2


def test_well_formed_unassigned_legacy_can_be_viewed_and_exported(console):
    client, headers, _ = console
    user = populate()[0]
    folder = user.store.folder("users/old-user-0123456789abcdef")
    (folder / "memory.json").write_text(
        json.dumps(
            {
                "version": 1,
                "revision": 0,
                "items": [
                    {
                        "id": "m_0123456789abcdef",
                        "kind": "fact",
                        "title": "old",
                        "text": "LEGACY ONLY",
                        "status": "",
                    }
                ],
            }
        )
    )
    assert "LEGACY ONLY" not in client.get("/maintenance/memory").text
    unlock(client, headers)
    assert "LEGACY ONLY" in client.get("/maintenance/memory").text
    assert "LEGACY ONLY" in client.get("/maintenance/memory/export").text
    assert json.loads((folder / "memory.json").read_text())["version"] == 1


def test_school_test_turns_are_never_queued_or_archived(console, monkeypatch):
    client, headers, _ = console

    def private_answer(system, message, knowledge="", selected=None):
        selected.private_turn = True
        return "PRIVATE SCHOOL RESPONSE"

    monkeypatch.setattr(admin, "llm_answer", private_answer)
    response = client.post(
        "/test-preset",
        json={
            "preset_key": "main",
            "memory_user": "Alice",
            "message": "Montre mes devoirs pour demain.",
        },
        headers=headers,
    )
    assert response.json["ok"]
    identifier = None
    with client.session_transaction() as cookie:
        identifier = cookie["memory_session"]
    selected = admin.MEMORY_SESSIONS[identifier]["memory"]
    with sql.connection(selected.store) as db:
        assert db.execute("SELECT count(*) FROM memory_events").fetchone()[0] == 0
    logs = list(selected.store.folder(selected.key).glob("logs/*.jsonl"))
    assert all(
        "PRIVATE SCHOOL RESPONSE" not in p.read_text()
        and "devoirs" not in p.read_text()
        for p in logs
    )
    client.post("/logout", headers=headers)
