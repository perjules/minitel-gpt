"""Session-only admin gate; synthetic secrets, real SQL, simulated UART/providers."""

import subprocess
from queue import Queue
from unittest.mock import Mock

import pytest
from flask import render_template_string
from test_memory_administration import change, selected
from test_rose_echo import RoutingSerial, submitted
from test_terminal_startup import EndOfInput, fn, screen_text

from services import admin_service_pages, memory_session, rose_terminal
from services import admin_ui as admin
from services import memory_database as sql
from services import minitel_chatgpt as chat

PASSWORD = "SYNTHETIC_admin_q7"


def activate(subject):
    ticket = subject.begin_household_unlock()
    assert subject.unlock_household(PASSWORD, PASSWORD, ticket)
    return ticket


def assert_no_secret(root, *secrets):
    for path in root.rglob("*"):
        if path.is_file():
            raw = path.read_bytes()
            assert all(secret.encode() not in raw for secret in secrets)


def test_explicit_unlocked_writes_publish_household_and_stay_isolated():
    default, other_default, alice = selected(""), selected(""), selected("Alice")
    try:
        change(alice, text="PRIVATE ALICE FACT")
        activate(default)
        default.model.plan.return_value = {"operation": "save", "kind": "fact", "text": "The household owns a Thermomix."}
        answer = default.handle("Souviens-toi que le foyer possède un Thermomix")
        assert "Enregistré" in answer["display"]
        item = default.store.read(default.key)["items"][0]
        assert item["origin"] == "household"
        assert "Thermomix" in alice.context("Thermomix") and "Thermomix" in other_default.context("Thermomix")
        assert default.model.plan.call_args.kwargs["household"]
        assert "lecture seule" in other_default.handle("Retiens une autre information")["display"]
        assert "PRIVATE ALICE" not in str(default.model.plan.call_args)
        default.record("user", "The family also owns a dishwasher.")
        with sql.connection(default.store) as db:
            assert db.execute("SELECT count(*) FROM memory_events WHERE user_id=1").fetchone()[0] == 0
            assert not db.execute("SELECT auto_enabled FROM memory_state WHERE user_id=1").fetchone()[0]
        default.model.plan.return_value = {"operation": "replace", "ids": [item["id"]], "text": "The household owns a newer Thermomix."}
        assert "Corrigé" in default.handle("Corrige le Thermomix")["display"]
        assert default.store.read(default.key)["items"][0]["origin"] == "household"
        default.model.plan.return_value = {"operation": "remove", "ids": [item["id"]]}
        assert "Retiré" in default.handle("Oublie le Thermomix")["display"]
        assert "Thermomix" not in alice.context("Thermomix")
        assert_no_secret(default.store.root, PASSWORD)
    finally:
        for subject in (default, other_default, alice):
            subject.close()


@pytest.mark.parametrize("kind", ["note", "list", "task"])
def test_unlocked_household_cannot_publish_nonhousehold_types(kind):
    subject = selected("")
    try:
        activate(subject)
        subject.model.plan.return_value = {"operation": "save", "kind": kind, "title": "PRIVATE", "text": "PRIVATE"}
        assert "seulement des faits" in subject.handle("Retiens ce contenu")["display"]
        assert not subject.store.read(subject.key)["items"]
    finally:
        subject.close()


def test_relock_reauthentication_cannot_authorize_delayed_old_model_work():
    subject = selected("")
    try:
        activate(subject)

        def delayed(*args, **kwargs):
            subject.handle("DESACTIVER MEMOIRE")
            activate(subject)
            return {"operation": "save", "kind": "fact", "text": "STALE FACT"}

        subject.model.plan.side_effect = delayed
        assert "lecture seule" in subject.handle("Retiens le fait précédent")["display"]
        assert not subject.store.read(subject.key)["items"]
        ticket = subject._household_ticket
        subject.close()
        with pytest.raises(memory_session.memory.MemoryProblem):
            subject.unlock_household(PASSWORD, PASSWORD, ticket)
        fresh = selected("")
        try:
            assert "lecture seule" in fresh.handle("Retiens le fait précédent")["display"]
        finally:
            fresh.close()
    finally:
        subject.close()


def test_password_and_tickets_are_required_and_no_named_profile_can_gain_household_writes():
    default, alice = selected(""), selected("Alice")
    try:
        with pytest.raises(memory_session.memory.MemoryProblem):
            alice.begin_household_unlock()
        ticket = default.begin_household_unlock()
        assert not alice.unlock_household(PASSWORD, PASSWORD, ticket)
        assert not default.unlock_household(PASSWORD, PASSWORD, "forged")
        assert not default.unlock_household("", "", ticket)
        assert not default.unlock_household(PASSWORD, PASSWORD, ticket)  # Wrong attempt retired it.
        ticket = activate(default)
        default.lock_household(ticket)
        assert not default.unlock_household(PASSWORD, PASSWORD, ticket)
        assert not default._household_unlocked
    finally:
        default.close()
        alice.close()


@pytest.mark.parametrize("direct,modem", [(False, True), (True, False), (False, False), (True, True)])
def test_terminal_password_is_hidden_and_original_echo_routes_restored(direct, modem, monkeypatch):
    subject = selected("")
    monkeypatch.setenv("ADMIN_PASSWORD", PASSWORD)
    monkeypatch.setattr(chat.time, "sleep", lambda _: None)
    serial = RoutingSerial(submitted(PASSWORD), direct=direct, modem=modem)
    term = chat.Term(serial)
    before = dict(serial.routes)
    try:
        action, text = chat.terminal_memory_unlock(term, subject)
        assert action == "done" and "activée" in text
        assert serial.echo == [(c, 0) for c in PASSWORD]
        assert PASSWORD.encode() not in serial.output and serial.routes == before
        assert_no_secret(subject.store.root, PASSWORD)
        subject.model.plan.assert_not_called()
    finally:
        subject.close()


@pytest.mark.parametrize("keys", [submitted("wrong-secret"), submitted("x" * 2001), fn(chat.K_ANNUL), fn(chat.K_SOMMAIRE)])
def test_cancel_wrong_and_oversized_passwords_leave_memory_locked(keys, monkeypatch):
    subject = selected("")
    monkeypatch.setenv("ADMIN_PASSWORD", PASSWORD)
    monkeypatch.setattr(chat.time, "sleep", lambda _: None)
    serial = RoutingSerial(keys)
    try:
        chat.terminal_memory_unlock(chat.Term(serial), subject)
        assert not subject._household_unlocked
        assert all(copies == 0 for _, copies in serial.echo)
        assert_no_secret(subject.store.root, "wrong-secret", PASSWORD)
    finally:
        subject.close()


def test_timeout_and_unavailable_echo_never_unlock_or_restore_secret_typeahead(monkeypatch):
    subject = selected("")
    monkeypatch.setenv("ADMIN_PASSWORD", PASSWORD)
    monkeypatch.setattr(chat.time, "sleep", lambda _: None)
    term = chat.Term(RoutingSerial())
    try:
        monkeypatch.setattr(chat, "read_memory_password", lambda _: (None, "timeout"))
        assert chat.terminal_memory_unlock(term, subject)[0] == "timeout"
        assert not subject._household_unlocked
        monkeypatch.setattr(rose_terminal, "hidden_echo", Mock(side_effect=rose_terminal.EchoUnavailable()))
        assert "indisponible" in chat.terminal_memory_unlock(term, subject)[1]
        assert not subject._household_unlocked
    finally:
        subject.close()


def test_real_uart_unlock_save_inventory_relock_and_next_session(monkeypatch):
    subject = selected("")
    subject.model.plan.return_value = {"operation": "save", "kind": "fact", "text": "The household owns a Thermomix."}
    keys = b"".join(submitted(q) for q in ("ACTIVER MEMOIRE", PASSWORD, "RETIENS le Thermomix", "MEMOIRE GLOBALE",
                                          "DESACTIVER MEMOIRE", "RETIENS encore le Thermomix")) + fn(chat.K_SOMMAIRE)
    serial = RoutingSerial(keys)
    serial.keys.append(EndOfInput())
    term = chat.Term(serial)
    monkeypatch.setenv("ADMIN_PASSWORD", PASSWORD)
    monkeypatch.setenv("GMAIL_ENABLED", "0")
    monkeypatch.setattr(chat.time, "sleep", lambda _: None)
    monkeypatch.setattr(chat, "wait_connection", lambda _: None)
    monkeypatch.setattr(chat, "prepare_boot", lambda _: [])
    monkeypatch.setattr(chat, "show_boot", lambda *args: "done")
    monkeypatch.setattr(chat.deployment, "available", lambda _: False)
    monkeypatch.setattr(chat, "select_memory_user", lambda *a, **kw: (subject, "envoi"))

    def synchronous(*args, **kwargs):
        result = chat.call_answer(*args, **kwargs)
        output = Queue()
        output.put((result, None))
        return Mock(is_alive=lambda: False), output, chat.time.monotonic()

    monkeypatch.setattr(chat, "start_answer", synchronous)
    with pytest.raises(EndOfInput):
        chat.run(term)
    assert subject.closed and not subject._household_unlocked
    assert "Thermomix" in screen_text(serial.output)
    assert PASSWORD.encode() not in serial.output
    assert_no_secret(subject.store.root, PASSWORD)
    subject.model.plan.assert_called_once()
    assert subject.store.read(subject.key)["items"][0]["origin"] == "household"


def test_guide_no_longer_discloses_admin_password(monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", PASSWORD)
    serial = RoutingSerial(fn(chat.K_ENVOI))
    monkeypatch.setattr(chat.time, "sleep", lambda _: None)
    monkeypatch.setattr(chat, "get_ip", lambda: "192.0.2.1")
    chat.show_guide(chat.Term(serial))
    assert PASSWORD.encode() not in serial.output
    assert "mot de passe admin" in screen_text(serial.output)


def test_browser_gate_requires_csrf_password_current_ticket_and_separate_secret_route(console, monkeypatch):
    client, headers, _ = console
    public = Mock(side_effect=AssertionError("Secret/control must not reach a model"))
    monkeypatch.setattr(admin, "llm_answer", public)
    begin = lambda name="": client.post("/test-preset", headers=headers, json={
        "preset_key": "main", "memory_user": name, "message": "ACTIVER MEMOIRE"})
    ticket = begin().json["memory_unlock"]
    payload = {"action": "unlock", "ticket": ticket, "password": admin.ADMIN_PASSWORD}
    assert client.post("/memory-unlock", json=payload).status_code == 403
    assert not client.post("/memory-unlock", headers=headers, json={**payload, "ticket": "forged"}).json["unlocked"]
    assert client.post("/memory-unlock", headers=headers, json=payload).json["unlocked"]
    with client.session_transaction() as cookie:
        current = admin.MEMORY_SESSIONS[cookie["memory_session"]]["memory"]
        assert "password" not in cookie
    assert current._household_unlocked
    next_ticket = begin().json["memory_unlock"]
    assert not current._household_unlocked
    assert not client.post("/memory-unlock", headers=headers, json=payload).json["unlocked"]
    assert client.post("/memory-unlock", headers=headers, json={**payload, "ticket": next_ticket}).json["unlocked"]
    client.post("/memory-unlock", headers=headers, json={"action": "lock", "ticket": ticket})
    assert current._household_unlocked  # An old cancellation cannot relock a new grant.
    client.post("/test-preset", headers=headers, json={"preset_key": "main", "message": "SOMMAIRE"})
    assert current.closed and not current._household_unlocked
    assert "memory_unlock" not in begin("Alice").json
    assert_no_secret(current.store.root, admin.ADMIN_PASSWORD)
    public.assert_not_called()


def test_browser_frontend_password_never_uses_the_ordinary_message_route(console):
    with admin.app.test_request_context():
        source = render_template_string(admin_service_pages.CHAT_TEST, preset="main", draft=None, username="")
    script = source.split("<script>")[1].split("</script>")[0]
    harness = '''
const assert=require('node:assert/strict');
const nodes={testMsg:{value:'ACTIVER MEMOIRE'},memoryUser:{value:''},testSpin:{style:{}},testOut:{},
 memoryUnlock:{hidden:true},memoryPassword:{value:'',focus(){}}};
global.document={getElementById:(id)=>nodes[id]};const calls=[];
global.fetch=async(url,args)=>{const body=JSON.parse(args.body);calls.push({url,body});
 return {json:async()=>url==='/test-preset'?{ok:true,memory_unlock:'ticket',answer:'Password'}:
  {ok:true,unlocked:body.action==='unlock',answer:'Done'}};};
'''
    checks = '''
(async()=>{await testPreset();assert.equal(nodes.memoryUnlock.hidden,false);
 nodes.memoryPassword.value='SYNTHETIC_BROWSER_PASSWORD';await submitMemoryPassword();
 assert.equal(nodes.memoryPassword.value,'');assert.equal(nodes.memoryUnlock.hidden,true);
 assert.equal(calls[1].url,'/memory-unlock');assert.equal(calls[1].body.password,'SYNTHETIC_BROWSER_PASSWORD');
 assert(!JSON.stringify(calls.filter(c=>c.url==='/test-preset')).includes('SYNTHETIC_BROWSER_PASSWORD'));
 closeMemoryUnlock(true);assert.equal(calls[2].body.action,'lock');
 await new Promise(resolve=>setImmediate(resolve));
 memoryTicket='retry-lock';global.fetch=async()=>{throw Error('offline')};
 closeMemoryUnlock(true);await new Promise(resolve=>setImmediate(resolve));
 assert(nodes.testOut.textContent.includes('Verrouillage non confirmé'));
 assert.equal(memoryTicket,'retry-lock');
 let release;memoryTicket='pending-password';memoryPending=true;
 nodes.memoryPassword.value='CANCELLED_SYNTHETIC_PASSWORD';
 global.fetch=async(url,args)=>({json:async()=>JSON.parse(args.body).action==='unlock'?
  await new Promise(resolve=>{release=resolve}):{ok:true}});
 const inFlight=submitMemoryPassword();await new Promise(resolve=>setImmediate(resolve));
 ++testSequence;closeMemoryUnlock(true);nodes.testOut.textContent='CANCELLED';
 release({ok:true,unlocked:true,answer:'STALE UNLOCK SUCCESS'});await inFlight;
 assert.equal(nodes.testOut.textContent,'CANCELLED');
})().catch(e=>{console.error(e);process.exit(1)});
'''
    result = subprocess.run(["node", "-e", harness + script + checks], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


def test_browser_actual_unlocked_write_is_shared_and_password_never_enters_model(console, monkeypatch):
    client, headers, _ = console
    ticket = client.post("/test-preset", headers=headers, json={
        "preset_key": "main", "message": "ACTIVER MEMOIRE appended-mistake"}).json["memory_unlock"]
    with client.session_transaction() as cookie:
        subject = admin.MEMORY_SESSIONS[cookie["memory_session"]]["memory"]
    subject.model = Mock()
    subject.model.plan.return_value = {"operation": "save", "kind": "fact", "text": "The household owns a Thermomix."}
    client.post("/memory-unlock", headers=headers, json={
        "action": "unlock", "ticket": ticket, "password": admin.ADMIN_PASSWORD})
    result = client.post("/test-preset", headers=headers, json={
        "preset_key": "main", "message": "RETIENS que le foyer a un Thermomix"})
    assert result.json["ok"] and "Enregistré" in result.json["answer"]
    assert subject.store.read(subject.key)["items"][0]["origin"] == "household"
    assert admin.ADMIN_PASSWORD not in str(subject.model.plan.call_args)
    assert_no_secret(subject.store.root, admin.ADMIN_PASSWORD, "appended-mistake")
    client.post("/test-preset", headers=headers, json={"preset_key": "main", "message": "DESACTIVER MEMOIRE"})
    result = client.post("/test-preset", headers=headers, json={
        "preset_key": "main", "message": "RETIENS autre chose"})
    assert "lecture seule" in result.json["answer"]
    subject.model.plan.assert_called_once()


def test_browser_missing_or_expired_challenge_never_creates_or_revives_a_session(console, monkeypatch):
    client, headers, _ = console
    request = {"action": "unlock", "ticket": "unknown", "password": admin.ADMIN_PASSWORD}
    assert client.post("/memory-unlock", headers=headers, json=request).status_code == 409
    ticket = client.post("/test-preset", headers=headers, json={
        "preset_key": "main", "message": "ACTIVER MEMOIRE"}).json["memory_unlock"]
    with client.session_transaction() as cookie:
        item = admin.MEMORY_SESSIONS[cookie["memory_session"]]
    item["used"] = chat.time.monotonic() - 1201
    assert client.post("/memory-unlock", headers=headers, json={**request, "ticket": ticket}).status_code == 409
    assert item["memory"].closed and not item["memory"]._household_unlocked


def test_empty_admin_configuration_and_named_terminal_do_not_request_a_password(monkeypatch):
    default, alice = selected(""), selected("Alice")
    monkeypatch.setenv("ADMIN_PASSWORD", "")
    try:
        activate(default)
        term = chat.Term(RoutingSerial())
        echo = Mock(side_effect=AssertionError("Do not ask for a secret"))
        monkeypatch.setattr(rose_terminal, "hidden_echo", echo)
        assert "Configurez" in chat.terminal_memory_unlock(term, default)[1]
        assert not default._household_unlocked
        assert "réservé" in chat.terminal_memory_unlock(term, alice)[1]
        echo.assert_not_called()
    finally:
        default.close()
        alice.close()
