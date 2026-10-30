"""Real SQLite and synthetic services: identity and private-resource boundaries."""

import importlib.util
import json
import re
import sqlite3
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
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2


def test_failed_alias_or_account_assignment_rolls_back(tmp_path):
    r, a, _b = setup_registry(tmp_path)
    for aliases, account in [(["Bob"], A), (["Alice"], "c" * 32)]:
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


@pytest.mark.parametrize("name", ["MinitelGPT", "MINITELGPT", " minitelgpt ", "ＭinitelGPT"])
def test_default_display_name_selects_reserved_profile_instead_of_creating_a_user(tmp_path, monkeypatch, name):
    reports = Mock()
    monkeypatch.setattr(user_registry.Registry, "report_event", reports)
    registry = user_registry.Registry(tmp_path)
    assert registry.resolve(name) == registry.resolve("")
    assert registry.resolve(name).id == 1
    assert registry.resolve(name).memory_key == "shared"
    assert len(registry.users()) == 1
    reports.assert_not_called()


def test_reserved_name_cannot_be_claimed_by_another_profiles_name_or_alias(tmp_path):
    registry = user_registry.Registry(tmp_path)
    user = registry.resolve("Alice")
    for name, aliases in [("MinitelGPT", []), ("Alice", [" MINITELGPT "])]:
        with pytest.raises(auth.GmailError, match="reserved_user_name"):
            registry.configure(user.id, name, True, aliases, None, False, [])
        assert registry.get(user.id).display_name == "Alice"
    registry.configure(1, "MinitelGPT", True, ["MinitelGPT"], None, False, [])
    assert registry.resolve("minitelgpt").id == 1


def test_existing_duplicate_remains_accessible_and_rename_preserves_its_data(console):
    client, _headers, _ = console
    registry = user_registry.Registry()
    default = registry.resolve("")
    key, name = user_registry.memory.user_key("MinitelGPT")
    # Reproduce the rows written by the old resolver, without running a migration.
    with registry.connection() as db:
        db.execute("INSERT INTO users(id,display_name,memory_key) VALUES(6,?,?)", (name, key))
        db.execute("INSERT INTO user_aliases VALUES('minitelgpt',6)")
        db.execute("INSERT INTO user_onboarding(user_id) VALUES(6)")
    folder = user_registry.memory.MemoryStore(registry.root).folder(key)
    private = folder / "private-fixture.enc"
    private.write_bytes(b"opaque private data")
    registry.record_account(A, "old-profile@example.org", 1)
    with registry.connection() as db:
        db.execute("INSERT INTO user_gmail_access VALUES(6,?,1,1)", (A,))
    assert registry.resolve("MinitelGPT").id == 6
    assert registry.resolve("") == default
    assert len(registry.users()) == 2
    assert "Ce profil existant et ses données sont conservés" in client.get("/users").text
    registry.configure(6, "Ancien test", True, [], A, True, [])
    assert registry.resolve("MinitelGPT").id == 1
    assert registry.resolve("Ancien test").id == 6
    assert registry.resolve("Ancien test").memory_key == key
    assert registry.gmail(6, "send")[1] == A
    assert private.read_bytes() == b"opaque private data"


def test_admin_selectors_leave_default_blank_and_keep_aliases_on_the_same_session(console):
    client, headers, _ = console
    registry = user_registry.Registry()
    for url, selector in [("/test", 'id=memoryUser'), ("/test?mode=gmail", 'name=username'),
                          ("/journal", 'id=username')]:
        page = client.get(url).text
        assert re.search(r'<input[^>]*' + selector + r'[^>]*value=""', page)
    # A still-open older page can submit its automatically filled display label.
    response = client.post("/test-preset", headers=headers,
        json={"preset_key": "main", "message": "", "memory_user": "MinitelGPT", "reset": True})
    assert response.status_code == 200 and response.json["ok"]
    assert len(registry.users()) == 1
    with admin.app.test_request_context():
        first = admin.memory_entry("")
        try:
            assert admin.memory_entry("MINITELGPT") is first
        finally:
            admin.discard_memory(admin.session.get("memory_session"))
    user = registry.resolve("Alice")
    response = client.post("/users", headers=headers, data={
        "action": "save", "user_id": user.id, "name": "MinitelGPT", "aliases": "Alice", "enabled": "on"})
    assert response.status_code == 400
    assert "réservés au profil #1" in response.text
    assert registry.get(user.id).display_name == "Alice"
    registry.configure(user.id, "Alicia", True, ["Alice"], None, False, [])
    with admin.app.test_request_context():
        first = admin.memory_entry("Alice")
        try:
            first.recent.append({"role": "user", "text": "Retain this turn"})
            assert admin.memory_entry("Alicia") is first
            assert first.recent[-1]["text"] == "Retain this turn"
        finally:
            admin.discard_memory(admin.session.get("memory_session"))


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
        "account_id": "c" * 32,
    }
    assert client.post("/users", data=form, headers=headers).status_code == 400
    assert r.gmail(a.id)[1] == A
    form["account_id"] = B
    assert client.post("/users", data=form, headers=headers).status_code == 200
    assert r.gmail(a.id)[1] == r.gmail(_b.id)[1] == B
    page = client.get("/users").text
    assert page.count(f'<option value="{B}" selected>') == 2
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
    assert r.accounts()[0]["users"] == []
    assert r.path.with_name("users.before-multi.sqlite3").is_file()
    with pytest.raises(auth.GmailError):
        r.gmail(1)


def test_shared_mailbox_keeps_independent_permissions_and_no_implicit_access(tmp_path):
    r, a, b = setup_registry(tmp_path)
    r.configure(b.id, "Bob", True, ["Bob"], A, False, ["Sam"])
    default = r.resolve("")
    r.configure(default.id, default.display_name, True, [], A, True, [])
    assert r.gmail(a.id, "send")[1] == r.gmail(b.id)[1] == r.gmail(1, "send")[1] == A
    with pytest.raises(auth.GmailError):
        r.gmail(b.id, "send")
    with pytest.raises(auth.GmailError):
        r.gmail(r.resolve("New").id)
    accounts = r.accounts()
    assert len(accounts) == 2
    shared = next(account for account in accounts if account["id"] == A)
    assert {u["user_id"] for u in shared["users"]} == {1, a.id, b.id}
    assert r.details(b.id)["can_send"] == 0
    with r.connection() as db, pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO user_gmail_access VALUES(?,?,1,0)", (a.id, B))
    # Replacing one user's mailbox leaves all other users on the shared account.
    r.configure(b.id, "Bob", True, ["Bob"], B, False, [])
    assert r.gmail(b.id)[1] == B and r.gmail(a.id)[1] == r.gmail(1)[1] == A


def test_shared_mailbox_session_revocation_is_per_user_but_reauthorization_is_global():
    r, a, b = setup_registry()
    r.configure(b.id, "Bob", True, ["Bob"], A, False, [])
    alice = user_services.ProfileTokens(a, r)
    bob = user_services.ProfileTokens(r.get(b.id), r)
    alice.guard("send")
    bob.guard()
    r.configure(b.id, "Bob", True, ["Bob"], None, False, [])
    with pytest.raises(auth.GmailError):
        bob.guard()
    alice.guard("send")
    r.configure(b.id, "Bob", True, ["Bob"], A, False, [])
    bob = user_services.ProfileTokens(r.get(b.id), r)
    bob.guard()
    r.record_account(A, "alice@example.org", 2)
    for tokens in (alice, bob):
        with pytest.raises(auth.GmailError, match="changed"):
            tokens.guard()
    assert r.gmail(a.id)[2] == r.gmail(b.id)[2] == 2


@pytest.mark.parametrize("action", ["disable", "disconnect"])
def test_account_controls_affect_all_explicitly_assigned_users(
    console, monkeypatch, action
):
    client, headers, _ = console
    r, a, b = setup_registry()
    r.configure(b.id, "Bob", True, ["Bob"], A, False, [])
    page = client.get("/services/gmail").text
    assert "Alice (#" in page and "Bob (#" in page
    assert (
        page.count("alice@example.org") == 2
    )  # One account card and one check option.
    helper = Mock(
        side_effect=lambda request: (
            {"status": "saved"}
            if request["operation"] == "forget"
            else (
                {A: {"email": "alice@example.org", "revision": 1}}
                if action == "disable"
                else {}
            )
        )
    )
    monkeypatch.setattr(auth, "credential_helper", helper)
    response = client.post(
        "/services/gmail/accounts",
        headers=headers,
        data={"action": action, "account_id": A},
    )
    assert response.status_code == 303
    for user in (a, b):
        with pytest.raises(auth.GmailError):
            r.gmail(user.id)
        assert r.get(user.id).revision > user.revision


def test_shared_mailbox_oauth_targets_existing_enrollment(console, monkeypatch):
    client, headers, _ = console
    r, _a, b = setup_registry()
    r.configure(b.id, "Bob", True, ["Bob"], A, False, [])
    origin = "https://pi.synthetic.ts.net:9443"
    with client.session_transaction(base_url=origin) as state:
        state.update(logged_in=True, csrf_token="test-token")
    monkeypatch.setattr(
        admin.gmail_oauth,
        "configuration",
        lambda: {"redirect_uri": origin + auth.OAUTH_CALLBACK},
    )
    begin = Mock(return_value=("attempt", "https://accounts.google.com/synthetic"))
    monkeypatch.setattr(admin.gmail_oauth, "begin", begin)
    result = client.post(
        "/services/gmail/oauth/start",
        headers=headers,
        base_url=origin,
        data={"user_id": b.id},
    )
    assert result.status_code == 303
    target = begin.call_args.args[1]
    assert (
        target["account_id"] == A
        and target["user_id"] == b.id
        and target["revision"] == 1
    )


def test_version_one_upgrade_preserves_data_and_backup_under_concurrent_use(tmp_path):
    r, a, b = setup_registry(tmp_path)
    # Recreate the exact restriction shipped in schema v1, with real existing grants.
    with sqlite3.connect(r.path) as db:
        db.executescript("""
ALTER TABLE user_gmail_access RENAME TO access_current;
CREATE TABLE user_gmail_access(user_id INTEGER PRIMARY KEY REFERENCES users(id),
 account_id TEXT NOT NULL UNIQUE REFERENCES gmail_accounts(id),
 can_read INTEGER NOT NULL CHECK(can_read IN (0,1)),
 can_send INTEGER NOT NULL CHECK(can_send IN (0,1) AND can_send<=can_read));
INSERT INTO user_gmail_access SELECT * FROM access_current;
DROP TABLE access_current;
PRAGMA user_version=1;
""")
        before = {
            name: db.execute("SELECT * FROM " + name).fetchall()
            for name in (
                "users",
                "user_aliases",
                "gmail_accounts",
                "user_gmail_access",
                "user_pronote_children",
            )
        }
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(
            executor.map(
                lambda _: user_registry.Registry(tmp_path).resolve("Alice"), range(4)
            )
        )
    backup = tmp_path / "users.before-shared-gmail.sqlite3"
    assert backup.stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(backup) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        assert {
            name: db.execute("SELECT * FROM " + name).fetchall() for name in before
        } == before
    with r.connection() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
        assert {
            name: [tuple(row) for row in db.execute("SELECT * FROM " + name)]
            for name in before
        } == before
        assert not db.execute("PRAGMA foreign_key_check").fetchall()
    assert r.get(a.id).memory_key == a.memory_key and r.get(b.id).revision == b.revision
    r.configure(b.id, "Bob", True, ["Bob"], A, False, ["Sam"])
    assert r.gmail(b.id)[1] == r.gmail(a.id)[1] == A
