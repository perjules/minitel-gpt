"""Isolated administration configuration shared by offline UI tests."""
from unittest.mock import Mock

import pytest

from services import admin_ui as admin
from services import config_store, system_health


@pytest.fixture
def console(tmp_path, monkeypatch):
    monkeypatch.setattr(admin, "ADMIN_PASSWORD", "synthetic-admin-password")
    prompts = tmp_path / "prompts.json"
    config_store.save_prompts(prompts, {"active": "main", "presets": {
        "main": {"label": "Main", "system": "synthetic prompt"},
        "other": {"label": "Other", "system": "another synthetic prompt"}}})
    for name, path in (("PROMPTS_FILE", prompts), ("ENV_FILE", tmp_path / ".env"),
                       ("KNOWLEDGE_DIR", tmp_path / "knowledge"),
                       ("DEPLOYMENT_STATUS", tmp_path / "deployment.json")):
        monkeypatch.setattr(admin, name, path)
    monkeypatch.setattr(admin, "svc_status", lambda _: "active")
    monkeypatch.setattr(admin, "ip_address", lambda: "192.0.2.1")
    monkeypatch.setattr(admin, "log_tail", lambda _, n=40: "\n".join(f"line {n}" for n in range(40)))
    monkeypatch.setattr(admin, "current_version", lambda: "test-version")
    monkeypatch.setattr(system_health, "command", lambda *args: "")
    monkeypatch.setattr(admin, "restart_terminal", Mock(return_value=True))
    client = admin.app.test_client()
    with client.session_transaction() as session:
        session.update(logged_in=True, csrf_token="test-token")
    return client, {"X-CSRF-Token": "test-token"}, prompts

