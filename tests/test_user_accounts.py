"""Real SQLite and synthetic services: identity and private-resource boundaries."""

import importlib.util
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from services import admin_ui as admin
from services import gmail_auth as auth
from services import gmail_client, gmail_mail, user_registry, user_services

A, B = "a" * 32, "b" * 32


@pytest.fixture(autouse=True)
def enabled_gmail(monkeypatch):
    monkeypatch.setenv("GMAIL_ENABLED", "1")


def setup_registry(root=None):
    r = user_registry.Registry(root)
    alice, bob = r.resolve("Alice"), r.resolve("Bob")
    r.record_account(A, "alice@example.org", 1)
    r.record_account(B, "bob@example.org", 1)
    r.configure(alice.id, "Alice", True, ["alice"], A, True, ["Alex"])
    r.configure(bob.id, "Bob", True, ["bob"], B, False, ["Sam"])
    return r, r.resolve("Alice"), r.resolve("Bob")


def test_identity_memory_aliases_and_grants_survive_restart(tmp_path):
    r, a, b = setup_registry(tmp_path)
    assert r.resolve(" ALICE ") == a
    assert r.resolve("").memory_key == "shared"
    assert r.resolve("New").id not in (1, a.id, b.id)
    with pytest.raises(auth.GmailError):
        r.gmail(r.resolve("New").id)
    r.configure(a.id, "Alicia", True, ["Alice", "Alicia"], A, True, ["Alex"])
    again = user_registry.Registry(tmp_path)
    assert again.resolve("Alicia").memory_key == a.memory_key
    assert again.resolve("Alice").id == a.id
    assert again.children(b.id) == {"Sam"}
    assert r.path.stat().st_mode & 0o777 == 0o600
    with r.connection() as db:
        assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1


def test_failed_alias_or_account_assignment_rolls_back(tmp_path):
    r, a, _b = setup_registry(tmp_path)
    for aliases, account in [(["Bob"], A), (["Alice"], B), (["Alice"], "c" * 32)]:
        with pytest.raises(auth.GmailError):
            r.configure(a.id, "Alice", True, aliases, account, True, [])
        assert r.gmail(a.id)[1] == A and r.children(a.id) == {"Alex"}
    r.configure(a.id, "Alice", False, ["Alice"], None, False, [])
    with pytest.raises(auth.GmailError, match="disabled"):
        r.resolve("alice")
    assert len(r.users()) == 3


def test_concurrent_first_use_creates_one_identity(tmp_path):
    r = user_registry.Registry(tmp_path)
    with ThreadPoolExecutor(max_workers=4) as executor:
        users = list(executor.map(lambda _: r.resolve("Concurrent"), range(12)))
    assert len({u.id for u in users}) == 1 and len(r.users()) == 2


def test_corrupt_database_is_preserved_and_never_replaced(tmp_path):
    r = user_registry.Registry(tmp_path)
    r.path.write_bytes(b"not a database")
    with pytest.raises(auth.GmailError):
        r.resolve("Alice")
    assert r.path.read_bytes() == b"not a database"


def test_read_send_and_changed_grants_guard_cached_session(monkeypatch):
    r, _a, b = setup_registry()
    client = user_services.ProfileMailClient(b, r)
    request = Mock(return_value={})
    monkeypatch.setattr(gmail_client.GmailClient, "request", request)
    client.request("GET", "/messages", 99999999)
    with pytest.raises(auth.GmailError):
        client.request("POST", "/messages/send", 99999999, payload={})
    assert request.call_count == 1
    session = gmail_mail.MailSession(client=client, model=Mock(), username="Bob")
    client.list = Mock(return_value=[])
    session.handle("MAIL")
    r.configure(b.id, "Bob", True, ["Bob"], None, False, [])
    assert "account_assignment_changed" in session.handle("MAIL")["display"]
    assert client.list.call_count == 1
    with pytest.raises(auth.GmailError):
        client.request("GET", "/messages", 99999999)


def test_pronote_filters_schema_and_execution_before_backend_cache():
    r, a, _b = setup_registry()
    backend = Mock()
    backend.config.return_value = {"enabled": True, "children": {"Alex": {}, "Sam": {}}}
    backend.tools.return_value = [
        {
            "function": {
                "parameters": {"properties": {"child": {"enum": ["Alex", "Sam"]}}}
            }
        }
    ]
    scoped = user_services.ProfileSchool(backend, a, r)
    assert set(scoped.config()["children"]) == {"Alex"}
    assert scoped.tools()[0]["function"]["parameters"]["properties"]["child"][
        "enum"
    ] == ["Alex"]
    assert scoped.execute("pronote_get_grades", {"child": "Sam"}) == {
        "error": "permission_denied"
    }
    backend.execute.assert_not_called()
    scoped.execute("pronote_get_grades", {"child": "Alex"})
    backend.execute.assert_called_once()
    r.configure(a.id, "Alice", True, ["Alice"], A, True, [])
    assert scoped.execute("pronote_get_grades", {"child": "Alex"}) == {
        "error": "permission_denied"
    }
    assert backend.execute.call_count == 1


def test_admin_profile_routes_enforce_csrf_and_transactional_assignments(console):
    client, headers, _ = console
    r, a, _b = setup_registry()
    assert (
        client.post("/users", data={"action": "create", "name": "New"}).status_code
        == 403
    )
    assert admin.app.test_client().get("/users").status_code == 302
    form = {
        "action": "save",
        "user_id": a.id,
        "name": "Alice",
        "aliases": "Alice",
        "enabled": "on",
        "account_id": B,
    }
    assert client.post("/users", data=form, headers=headers).status_code == 400
    assert r.gmail(a.id)[1] == A
    assert "alice@example.org" in client.get("/users").text


def helper_module(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "multi_helper", Path(__file__).parents[1] / "scripts/gmail_credential_helper.py"
    )
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "gmail_auth", auth)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "Path", lambda _: tmp_path / "lock")
    info = {
        "client_id": "fake.apps.googleusercontent.com",
        "client_secret": "secret",
        "refresh_token": "refresh-a",
        "token_uri": auth.TOKEN_URI,
        "scopes": list(auth.SCOPES),
        "email_address": "alice@example.org",
    }
    bundle = {
        "version": 1,
        "accounts": {
            A: {"info": info, "revision": 1},
            B: {
                "info": info
                | {"email_address": "bob@example.org", "refresh_token": "refresh-b"},
                "revision": 1,
            },
        },
    }
    monkeypatch.setattr(
        module, "account_bundle", lambda: json.loads(json.dumps(bundle))
    )
    save = Mock()
    monkeypatch.setattr(module, "save_accounts", save)
    monkeypatch.setattr(
        module,
        "decrypt_oauth_config",
        lambda: {k: info[k] for k in ("client_id", "client_secret")},
    )
    return module, info, save


def test_root_rotation_is_targeted_and_reauthorization_rejects_other_account(
    tmp_path, monkeypatch
):
    module, info, save = helper_module(tmp_path, monkeypatch)
    assert module.process(
        {
            "operation": "rotate",
            "account_id": A,
            "revision": 1,
            "expected": "refresh-a",
            "replacement": "new",
        }
    ) == {"status": "saved"}
    data = save.call_args.args[0]
    assert data["accounts"][A]["info"]["refresh_token"] == "new"
    assert data["accounts"][B]["info"]["refresh_token"] == "refresh-b"
    save.reset_mock()
    for request in [
        {
            "operation": "enroll",
            "account_id": A,
            "revision": 1,
            "info": info | {"email_address": "wrong@example.org"},
        },
        {"operation": "read", "account_id": "../bad", "revision": 1},
        {"operation": "read", "account_id": A, "revision": 2},
    ]:
        with pytest.raises(auth.GmailError):
            module.process(request)
    save.assert_not_called()


def test_root_metadata_contains_no_tokens(tmp_path, monkeypatch):
    module, _, _ = helper_module(tmp_path, monkeypatch)
    result = module.process({"operation": "accounts"})
    assert result[A] == {"email": "alice@example.org", "revision": 1}
    assert "refresh" not in json.dumps(result) and "secret" not in json.dumps(result)


def test_legacy_enroller_cannot_replace_multi_account_store(tmp_path, monkeypatch):
    store = tmp_path / "accounts"
    store.touch()
    monkeypatch.setattr(auth, "ACCOUNTS_STORE", store)
    info = {
        "client_id": "fake.apps.googleusercontent.com",
        "client_secret": "secret",
        "refresh_token": "refresh",
        "token_uri": auth.TOKEN_URI,
        "scopes": list(auth.SCOPES),
        "email_address": "a@example.org",
    }
    with pytest.raises(auth.GmailError, match="account_target_required"):
        auth.encrypt_info(info)


def test_cached_tokens_reset_on_401_and_grant_revision_change():
    r, a, _ = setup_registry()
    tokens = user_services.ProfileTokens(a, r)
    tokens.source = SimpleNamespace(credentials="cached")
    tokens.credentials = None
    assert tokens.source.credentials is None
    tokens.guard()
    r.record_account(A, "alice@example.org", 2)
    with pytest.raises(auth.GmailError, match="changed"):
        tokens.guard()


def test_school_denial_never_substitutes_an_allowed_child():
    r, a, _ = setup_registry()
    backend = Mock()
    backend.config.return_value = {"enabled": True, "children": {"Alex": {}, "Sam": {}}}
    school = user_services.ProfileSchool(backend, a, r)
    assert school.denied("Montre les notes de Sam")
    assert not school.denied("Montre les notes de Alex")


def test_pre_multi_account_release_is_refused_before_service_stop(tmp_path):
    from scripts.runtime import Runtime

    runtime = Runtime(
        tmp_path / "checkout",
        tmp_path / "runtime",
        tmp_path / "state",
        tmp_path / "uv",
        tmp_path / "system",
    )
    store = runtime.system_path("/etc/credstore.encrypted/minitel-gmail-accounts")
    store.parent.mkdir(parents=True)
    store.write_bytes(b"encrypted")
    release = tmp_path / "old-release"
    (release / "root").mkdir(parents=True)
    (release / "root/gmail_auth.py").write_text("legacy")
    runtime.running = Mock()
    with pytest.raises(RuntimeError, match="compatible release"):
        runtime.switch({"release": str(release)}, {}, branch="develop")
    runtime.running.assert_not_called()


def test_reassignment_during_enrollment_cannot_be_overwritten():
    r, a, _ = setup_registry()
    r.configure(a.id, "Alice", True, ["Alice"], None, False, [])
    with pytest.raises(auth.GmailError, match="changed"):
        r.record_account(A, "alice@example.org", 2, a.id, a.revision)
    with pytest.raises(auth.GmailError):
        r.gmail(a.id)


def test_permission_removal_during_mail_read_discards_result():
    r, a, _ = setup_registry()
    client = user_services.ProfileMailClient(a, r)

    def revoke(*args):
        r.configure(a.id, "Alice", True, ["Alice"], None, False, [])
        return [{"id": "private-message"}]

    client.list = Mock(side_effect=revoke)
    session = gmail_mail.MailSession(client=client, model=Mock(), username="Alice")
    result = session.handle("MAIL")
    assert "account_assignment_changed" in result["display"]
    assert session.items == []


def test_forget_one_account_preserves_the_other(tmp_path, monkeypatch):
    module, _, save = helper_module(tmp_path, monkeypatch)
    assert module.process({"operation": "forget", "account_id": A, "revision": 1}) == {
        "status": "saved"
    }
    assert set(save.call_args.args[0]["accounts"]) == {B}


def test_migration_retains_unassigned_account_and_backs_up_registry(
    console, monkeypatch
):
    client, headers, _ = console
    helper = Mock(
        side_effect=lambda request: (
            {"status": "saved", "revision": 1}
            if request["operation"] == "migrate"
            else {A: {"email": "legacy@example.org", "revision": 1}}
        )
    )
    monkeypatch.setattr(auth, "credential_helper", helper)
    response = client.post(
        "/services/gmail/accounts", data={"action": "migrate"}, headers=headers
    )
    assert response.status_code == 303
    r = user_registry.Registry()
    assert r.accounts()[0]["user_id"] is None
    assert r.path.with_name("users.before-multi.sqlite3").is_file()
    with pytest.raises(auth.GmailError):
        r.gmail(1)
