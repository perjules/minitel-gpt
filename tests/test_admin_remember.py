"""AI add button uses the actual RETIENS planner, guards and SQL save path."""

import json
import subprocess
import time
from unittest.mock import Mock

import pytest
from test_admin_memory import unlock
from test_memory_administration import selected

from services import admin_ui as admin
from services import memory_database as sql
from services import memory_session as sessions


def payload(subject, message="I enjoy chess."):
    return {"preset_key": "main", "memory_profile": subject.user.id,
            "memory_profile_revision": subject.user.revision, "message": message}


def planner(monkeypatch, proposal=None):
    complete = Mock(return_value=json.dumps(proposal or {
        "operation": "save", "kind": "preference", "text": "User enjoys chess."}))
    monkeypatch.setattr(sessions.MailModel, "complete", complete)
    return complete


def test_add_button_opens_a_gated_profile_bound_screen_without_model_or_session_creation(console, monkeypatch):
    client, headers, _ = console
    alice = selected("Alice")
    try:
        route = f"/maintenance/memory/{alice.user.id}/add"
        page = client.get("/maintenance/memory")
        assert "Ajouter une mémoire (IA)" in page.text and route in page.text
        gate = client.get(route)
        assert gate.status_code == 302 and "next=add" in gate.location
        assert "after_add" in client.get(gate.location).text
        unlocked = client.post("/maintenance/memory", headers=headers, data={
            "action": "unlock", "password": "synthetic-admin-password", "profile": alice.user.id, "after_add": "yes"})
        assert unlocked.location.endswith(route)
        constructor = Mock(side_effect=AssertionError("Opening the form must not open a chat or call AI"))
        monkeypatch.setattr(admin.memory_session, "MemorySession", constructor)
        page = client.get(route)
        assert page.status_code == 200
        assert "Ajouter une mémoire — Alice" in page.text
        assert "Ajouter avec l’IA" in page.text and "RETIENS" in page.text
        assert f'"id": {alice.user.id}' in page.text
        assert "nouveaux souvenirs restent privés" in page.text
        assert "Faire connaissance" not in page.text
        constructor.assert_not_called()
    finally:
        alice.close()


def test_natural_add_uses_real_interpreter_and_explicit_private_persistence(console, monkeypatch):
    client, headers, _ = console
    monkeypatch.setenv("MINITEL_ONBOARDING", "1")
    alice, bob = selected("Alice"), selected("Bob")
    complete = planner(monkeypatch)
    general = Mock(side_effect=AssertionError("Memory input must not go to ordinary chat"))
    monkeypatch.setattr(admin, "llm_answer", general)
    try:
        unlock(client, headers)
        request = payload(alice)
        request["memory_user"] = "Misspelled unknown person"
        result = client.post("/test-preset", headers=headers, json=request)
        assert result.json["ok"] and "Enregistré" in result.json["answer"]
        record = alice.store.read(alice.key)["items"][0]
        assert record["kind"] == "preference" and record["origin"] == "explicit" and not record["shared"]
        assert not bob.store.read(bob.key)["items"]
        assert "Misspelled unknown person" not in {r["display_name"] for r in alice.registry.users()}
        instructions, data, _ = complete.call_args.args
        assert data["request"] == "RETIENS I enjoy chess."
        assert "deliberately requested memory operation" in instructions
        with sql.connection(alice.store) as db:
            assert db.execute("SELECT count(*) FROM memory_events").fetchone()[0] == 0
            assert db.execute("SELECT turns FROM user_onboarding WHERE user_id=?", (alice.user.id,)).fetchone()[0] == 0
        general.assert_not_called()
        page = client.get(f"/maintenance/memory/{alice.user.id}")
        assert "User enjoys chess." in page.text and "saisie manuelle (sans IA)" in page.text
    finally:
        alice.close()
        bob.close()


def test_clarification_then_refinement_uses_same_memory_session_and_no_false_save(console, monkeypatch):
    client, headers, _ = console
    alice = selected("Alice")
    complete = planner(monkeypatch)
    complete.side_effect = [json.dumps({"operation": "clarify", "clarification": "Quelle adresse retenir ?"}),
                            json.dumps({"operation": "save", "kind": "fact", "text": "User lives at a synthetic address."})]
    try:
        unlock(client, headers)
        first = client.post("/test-preset", headers=headers, json=payload(alice, "mon adresse"))
        assert "Quelle adresse" in first.json["answer"]
        assert not alice.store.read(alice.key)["items"]
        result = client.post("/test-preset", headers=headers, json=payload(alice, "RETIENS mon adresse de test"))
        assert "Enregistré" in result.json["answer"]
        data = complete.call_args.args[1]
        assert data["request"] == "RETIENS mon adresse de test"
        assert any("Quelle adresse" in r["text"] for r in data["recent_visible_conversation"])
    finally:
        alice.close()


@pytest.mark.parametrize("proposal", [{"operation": "public"}, {"operation": "save", "kind": "invalid", "text": "bad"},
                                      {"operation": "save", "text": "bad", "grant_admin": True}])
def test_failed_or_unrelated_proposals_never_write_or_fall_back_to_chat(console, monkeypatch, proposal):
    client, headers, _ = console
    alice = selected("Alice")
    planner(monkeypatch, proposal)
    general = Mock(side_effect=AssertionError("No ordinary/integration fallback"))
    monkeypatch.setattr(admin, "llm_answer", general)
    try:
        unlock(client, headers)
        result = client.post("/test-preset", headers=headers, json=payload(alice))
        assert "Enregistré" not in result.json["answer"]
        assert not alice.store.read(alice.key)["items"]
        general.assert_not_called()
    finally:
        alice.close()


def test_default_add_keeps_password_gate_and_publishes_only_after_actual_unlock(console, monkeypatch):
    client, headers, _ = console
    default, alice = selected(""), selected("Alice")
    complete = planner(monkeypatch, {"operation": "save", "kind": "fact", "text": "The household owns a Thermomix."})
    try:
        unlock(client, headers)
        assert "Activer la mémoire du foyer" in client.get("/maintenance/memory/1/add").text
        first = client.post("/test-preset", headers=headers, json=payload(default, "Le foyer a un Thermomix"))
        assert "lecture seule" in first.json["answer"]
        complete.assert_not_called()
        ticket = client.post("/test-preset", headers=headers, json=payload(default, "ACTIVER MEMOIRE")).json["memory_unlock"]
        client.post("/memory-unlock", headers=headers, json={
            "action": "unlock", "ticket": ticket, "password": admin.ADMIN_PASSWORD})
        saved = client.post("/test-preset", headers=headers, json=payload(default, "Le foyer a un Thermomix"))
        assert "Enregistré" in saved.json["answer"]
        assert default.store.read(default.key)["items"][0]["origin"] == "household"
        assert "Thermomix" in alice.context("Thermomix")
        assert complete.call_args.args[1]["write_scope"] == "household"
        assert admin.ADMIN_PASSWORD not in str(complete.call_args)
    finally:
        default.close()
        alice.close()


def test_gate_csrf_and_expiry_protect_add_submission(console, monkeypatch):
    client, headers, _ = console
    alice = selected("Alice")
    complete = planner(monkeypatch)
    try:
        assert client.post("/test-preset", headers=headers, json=payload(alice)).status_code == 403
        unlock(client, headers)
        assert client.post("/test-preset", json=payload(alice)).status_code == 403
        with client.session_transaction() as cookie:
            cookie["memory_unlock_until"] = time.time() - 1
        assert client.post("/test-preset", headers=headers, json=payload(alice)).status_code == 403
        complete.assert_not_called()
        assert not alice.store.read(alice.key)["items"]
    finally:
        alice.close()


@pytest.mark.parametrize("change", ["rename", "delete", "disable"])
def test_stale_profile_id_does_not_create_or_write_another_profile(console, monkeypatch, change):
    client, headers, _ = console
    alice = selected("Alice")
    complete = planner(monkeypatch)
    try:
        unlock(client, headers)
        request = payload(alice)
        if change == "delete":
            alice.registry.delete(alice.user.id, alice.user.revision, alice.username)
        else:
            alice.registry.configure(alice.user.id, "Alicia", change != "disable", ["Alice"], "", False, [])
        before = alice.registry.users()
        assert not client.post("/test-preset", headers=headers, json=request).json["ok"]
        assert alice.registry.users() == before
        complete.assert_not_called()
    finally:
        alice.close()


def test_add_frontend_binds_id_and_preserves_household_draft_during_password_step(console):
    client, headers, _ = console
    unlock(client, headers)
    page = client.get("/maintenance/memory/1/add").text
    script = page.split("<script>")[1].split("</script>")[0]
    harness = '''
const assert=require('node:assert/strict');
const nodes={testMsg:{value:'Le foyer a un Thermomix'},memoryUser:{value:'wrong name'},testSpin:{style:{}},testOut:{},
 memoryUnlock:{hidden:true},memoryPassword:{value:'',focus(){}}};
global.document={getElementById:id=>nodes[id]};const calls=[];
global.fetch=async(url,args)=>{const body=JSON.parse(args.body);calls.push(body);return {json:async()=>
 body.message==='ACTIVER MEMOIRE'?{ok:true,memory_unlock:'ticket'}:{ok:true,answer:'Synthetic'}}};
'''
    checks = '''
(async()=>{await testPreset(false,false,'ACTIVER MEMOIRE');
 assert.equal(nodes.testMsg.value,'Le foyer a un Thermomix');assert.equal(nodes.memoryUnlock.hidden,false);
 assert.equal(calls[0].memory_profile,1);assert.equal(typeof calls[0].memory_profile_revision,'number');
})().catch(e=>{console.error(e);process.exit(1)});
'''
    result = subprocess.run(["node", "-e", harness + script + checks], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


def test_profile_change_during_interpretation_blocks_the_late_save(console, monkeypatch):
    client, headers, _ = console
    alice = selected("Alice")
    complete = planner(monkeypatch)

    def renamed(*args, **kwargs):
        alice.registry.configure(alice.user.id, "Alicia", True, ["Alice"], "", False, [])
        return json.dumps({"operation": "save", "kind": "fact", "text": "STALE FACT"})

    complete.side_effect = renamed
    try:
        unlock(client, headers)
        result = client.post("/test-preset", headers=headers, json=payload(alice))
        assert not result.json["ok"]
        assert not alice.store.read(alice.key)["items"]
    finally:
        alice.close()
