"""Real CSRF boundaries, durable configuration and constrained knowledge paths."""
import errno
import fcntl
import io
import json
import os
import re
import runpy
import stat
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock

import pytest

from services import admin_ui as admin
from services import config_store, io_limits, terminal_link


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setattr(admin, "ADMIN_PASSWORD", "synthetic-admin-password")
    prompts = tmp_path / "prompts.json"
    original = {"active": "main", "presets": {"main": {"label": "Main", "system": "private personality"}}}
    config_store.save_prompts(prompts, original)
    monkeypatch.setattr(admin, "PROMPTS_FILE", prompts)
    monkeypatch.setattr(admin, "ENV_FILE", tmp_path / ".env")
    monkeypatch.setattr(admin, "KNOWLEDGE_DIR", tmp_path / "knowledge")
    monkeypatch.setattr(admin, "restart_terminal", Mock(return_value=True))
    client = admin.app.test_client()
    with client.session_transaction() as session:
        session["logged_in"] = True
        session["csrf_token"] = "security-test-token"
    return client, {"X-CSRF-Token": "security-test-token"}, prompts, original


def test_all_mutation_routes_require_csrf_before_their_side_effects(settings):
    client, _, path, original = settings
    for route in ("/save-prompt", "/apply-preset", "/new-preset", "/delete-preset", "/save-llm",
                  "/upload-knowledge", "/delete-knowledge", "/restart", "/update", "/rollback",
                  "/check-update", "/test-preset", "/generate-prompt", "/preview-title", "/logout"):
        assert client.post(route, data={}).status_code == 403, route
    assert json.loads(path.read_text()) == original
    admin.restart_terminal.assert_not_called()


def test_valid_form_and_json_requests_work_and_rotate_token_on_login(settings, monkeypatch):
    client, headers, path, _ = settings
    response = client.post("/save-prompt", data={"preset_key": "main", "system_prompt": "updated",
                                                "csrf": "security-test-token"})
    assert response.status_code == 302
    assert json.loads(path.read_text())["presets"]["main"]["system"] == "updated"
    assert client.post("/preview-title", json={"segments": []}, headers=headers).status_code == 200
    assert client.post("/preview-title", json=[], headers=headers).status_code == 400
    assert client.get("/logout").status_code == 405
    assert client.post("/logout", headers=headers).status_code == 302
    assert client.post("/login", data={"password": admin.ADMIN_PASSWORD}).status_code == 403
    page = client.get("/login")
    token = re.search(r'name=csrf value="([^"]+)"', page.text)[1]
    assert client.post("/login", data={"password": admin.ADMIN_PASSWORD, "csrf": token}).status_code == 302
    with client.session_transaction() as session:
        assert session["logged_in"] and session["csrf_token"] != token


def test_settings_update_is_one_validated_transaction_and_rejects_env_injection(settings, monkeypatch):
    client, headers, _, _ = settings
    env = admin.ENV_FILE
    env.write_text("# keep comment\nMISTRAL_KEY=old\nLLM_PROVIDER=mistral\n")
    before = env.read_bytes()
    response = client.post("/save-llm", data={"llm_provider": "claude", "mistral_key": "new",
        "anthropic_key": "bad\nLD_PRELOAD=untrusted"}, headers=headers)
    assert response.status_code == 400 and env.read_bytes() == before
    admin.restart_terminal.assert_not_called()
    original_write = config_store.atomic_write
    writes = []
    def write(path, *args, **kwargs):
        writes.append(path)
        return original_write(path, *args, **kwargs)
    monkeypatch.setattr(config_store, "atomic_write", write)
    assert client.post("/save-llm", data={"llm_provider": "mistral", "mistral_key": "new"},
                       headers=headers).status_code == 302
    assert writes == [env]
    assert admin.read_env()["MISTRAL_KEY"] == "new"
    assert "# keep comment" in env.read_text() and stat.S_IMODE(env.stat().st_mode) == 0o600


def test_env_update_reconciles_duplicates_and_parses_quoted_values(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("# keep\nMISTRAL_KEY=old\nexport MISTRAL_KEY='also old'\nOTHER='preserve spaces'\n")
    config_store.update_env(env, {"MISTRAL_KEY": "new"})
    assert env.read_text().count("MISTRAL_KEY=") == 1
    monkeypatch.setattr(admin, "ENV_FILE", env)
    assert admin.read_env() == {"MISTRAL_KEY": "new", "OTHER": "preserve spaces"}
    for value in ("x\rOTHER=bad", "x\x85OTHER=bad", "x\u2028OTHER=bad", "x\0OTHER=bad"):
        with pytest.raises(config_store.InvalidConfig):
            config_store.update_env(env, {"MISTRAL_KEY": value})


def test_env_values_round_trip_and_multiline_owner_values_are_preserved(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text('# comment\nOTHER="owner\ntext"\nMISTRAL_KEY="old\nvalue"\nBARE_KEY\n')
    value = 'new # key with "quotes", \\ and apostrophe\'s'
    config_store.update_env(env, {"MISTRAL_KEY": value})
    monkeypatch.setattr(admin, "ENV_FILE", env)
    assert admin.read_env() == {"OTHER": "owner\ntext", "MISTRAL_KEY": value}
    assert '# comment\nOTHER="owner\ntext"\n' in env.read_text()
    env.write_text('MISTRAL_KEY="unfinished')
    before = env.read_bytes()
    with pytest.raises(config_store.InvalidConfig):
        config_store.update_env(env, {"MISTRAL_KEY": "new"})
    assert env.read_bytes() == before


def test_disk_full_or_replace_failure_preserves_original_configuration(tmp_path, monkeypatch):
    path = tmp_path / "config"
    path.write_bytes(b"original")
    path.chmod(0o640)
    monkeypatch.setattr(config_store.os, "fsync", Mock(side_effect=OSError(errno.ENOSPC, "full")))
    with pytest.raises(config_store.StorageFailure):
        config_store.atomic_write(path, b"replacement")
    assert path.read_bytes() == b"original" and stat.S_IMODE(path.stat().st_mode) == 0o640
    assert not list(tmp_path.glob(".minitel-config-*"))


def test_atomic_write_preserves_mode_and_rejects_symlink(tmp_path):
    path = tmp_path / "prompts"
    path.write_bytes(b"old")
    path.chmod(0o640)
    config_store.atomic_write(path, b"new")
    assert path.read_bytes() == b"new" and stat.S_IMODE(path.stat().st_mode) == 0o640
    link = tmp_path / "link"
    link.symlink_to(path)
    with pytest.raises(config_store.InvalidConfig):
        config_store.atomic_write(link, b"wrong")
    assert path.read_bytes() == b"new"


def test_concurrent_prompt_transactions_keep_every_edit(settings):
    _, _, path, _ = settings
    start = threading.Barrier(4)
    def edit(number):
        start.wait(timeout=2)
        with config_store.locked(path):
            data = config_store.read_prompts(path)
            data["presets"][f"p{number}"] = {"system": str(number)}
            config_store.save_prompts(path, data)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(edit, range(4)))
    assert set(config_store.read_prompts(path)["presets"]) == {"main", "p0", "p1", "p2", "p3"}


def test_lock_wait_is_bounded_when_another_open_description_holds_flock(tmp_path):
    path = tmp_path / "config"
    with Path(str(path) + ".lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with pytest.raises(config_store.StorageFailure, match="busy"), config_store.locked(path, timeout=.02):
            pytest.fail("must not enter while locked")


def test_invalid_json_and_schema_never_overwrite_owner_data(settings):
    client, headers, path, _ = settings
    path.write_text('{"active": broken owner data')
    before = path.read_bytes()
    assert client.post("/save-prompt", data={"preset_key": "main"}, headers=headers).status_code == 400
    assert path.read_bytes() == before
    for data in ({}, {"active": "missing", "presets": {}},
                 {"active": "../escape", "presets": {"../escape": {}}},
                 {"active": "p", "presets": {"p": {"title_art": "wrong"}}}):
        with pytest.raises(config_store.InvalidConfig):
            config_store.save_prompts(path, data)
        assert path.read_bytes() == before


def test_knowledge_deletion_cannot_escape_or_follow_links(settings, tmp_path):
    client, headers, _, _ = settings
    external = tmp_path / "owner.txt"
    external.write_text("preserve")
    folder = admin.KNOWLEDGE_DIR / "main"
    folder.mkdir(parents=True)
    (folder / "link.txt").symlink_to(external)
    for key, name in (("../../", "owner.txt"), (str(tmp_path), "owner.txt"),
                      ("main", "../owner.txt"), ("main", "link.txt"), ("main", "owner.key")):
        assert client.post("/delete-knowledge", data={"preset_key": key, "filename": name},
                           headers=headers).status_code == 400
    assert external.read_text() == "preserve" and (folder / "link.txt").is_symlink()
    folder.rename(folder.with_name("real"))
    folder.symlink_to(folder.with_name("real"), target_is_directory=True)
    assert client.post("/upload-knowledge", data={"preset_key": "main", "files":
        (io.BytesIO(b"x"), "new.txt")}, headers=headers).status_code == 400


def test_knowledge_upload_and_delete_allow_regular_files(settings):
    client, headers, _, _ = settings
    response = client.post("/upload-knowledge", data={"preset_key": "main", "files":
        (io.BytesIO(b"reference"), "notes.txt")}, headers=headers)
    assert response.status_code == 302
    path = admin.KNOWLEDGE_DIR / "main/notes.txt"
    assert path.read_bytes() == b"reference"
    assert client.post("/delete-knowledge", data={"preset_key": "main", "filename": "notes.txt"},
                       headers=headers).status_code == 302
    assert not path.exists()


def test_fifo_or_link_cannot_block_knowledge_loading(tmp_path):
    fifo = tmp_path / "pipe.txt"
    os.mkfifo(fifo)
    with pytest.raises(OSError):
        io_limits.read_knowledge(fifo)
    (tmp_path / "link.txt").symlink_to(fifo)
    with pytest.raises(OSError):
        io_limits.read_knowledge(tmp_path / "link.txt")


def test_html_forms_have_tokens_and_embedded_json_cannot_close_script(settings, monkeypatch):
    client, _, path, data = settings
    data["presets"]["main"]["system"] = '</script><script id=unexpected>alert(1)</script>'
    config_store.save_prompts(path, data)
    monkeypatch.setattr(admin, "svc_status", lambda _: "active")
    monkeypatch.setattr(admin, "ip_address", lambda: "127.0.0.1")
    monkeypatch.setattr(admin, "log_tail", lambda _, n=40: "log")
    monkeypatch.setattr(admin, "current_version", lambda: "test")
    page = client.get("/")
    assert page.status_code == 200
    assert "<script id=unexpected>" not in page.text
    assert r"\u003c/script\u003e" in page.text
    forms = [body for attrs, body in re.findall(r'<form\b([^>]*)>(.*?)</form>', page.text, re.DOTALL) if 'post' in attrs.lower()]
    assert forms and all('name=csrf' in form or 'name="csrf"' in form for form in forms)
    assert "X-CSRF-Token" in page.text


def test_direct_terminal_entry_uses_the_same_exception_identity(monkeypatch):
    serve = Mock()
    monkeypatch.setattr(terminal_link, "serve", serve)
    path = Path(__file__).resolve().parents[1] / "services/minitel_chatgpt.py"
    namespace = runpy.run_path(str(path), run_name="__main__")
    assert namespace["terminal_link"] is terminal_link
    serve.assert_called_once()
