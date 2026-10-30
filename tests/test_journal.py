"""Synthetic journals only: encryption, private state and date permissions."""

from datetime import date
from unittest.mock import Mock, patch

import pytest

from services import journal, user_registry
from services import journal_store as storage


@pytest.fixture
def vault(tmp_path):
    registry = user_registry.Registry(tmp_path / "users")
    return storage.Vault(registry.resolve("Julian"), registry)


@pytest.fixture
def diary(vault):
    model = Mock()
    model.draft.return_value = "Texte rédigé, sans faits inventés."
    session = journal.Session(vault, model=model, date=lambda: date(2026, 10, 30))
    session.open()
    session.handle("x")
    session.handle("x")
    yield session
    session.close()


def save(diary, text):
    result = diary.handle("TEXTE : " + text)
    assert result.get("review")
    diary.acknowledge()
    assert "enregistrée" in diary.handle("ENREGISTRER")["display"]


def test_password_only_ciphertext_and_authenticated_owner(vault):
    salt = b"s" * 16
    key = storage.derive("a", salt)
    days = {"2026-10-29": "Private synthetic diary paragraph."}
    fingerprint = vault.write(days, key, salt, None)
    path = vault.store.root / vault.user.memory_key / storage.FILENAME
    blob = path.read_bytes()
    assert b"Private synthetic" not in blob
    assert b"2026-10-29" not in blob
    assert path.stat().st_mode & 0o777 == 0o600
    assert vault.open("a")[2:] == (days, fingerprint)
    for password, owner in (("b", vault.user.id), ("a", vault.user.id + 1)):
        with pytest.raises(storage.JournalError):
            storage.decrypt(blob, password, owner)
    damaged = blob[:-1] + bytes([blob[-1] ^ 1])
    with pytest.raises(storage.JournalError):
        storage.decrypt(damaged, "a")


def test_no_default_file_and_no_default_profile(vault):
    assert not vault.exists()
    assert not (vault.store.root / vault.user.memory_key / storage.FILENAME).exists()
    with pytest.raises(storage.JournalError):
        storage.Vault(vault.registry.resolve(""), vault.registry)


def test_failed_save_preserves_old_ciphertext(diary):
    save(diary, "Before disk failure.")
    path = diary.vault.store.root / diary.vault.user.memory_key / storage.FILENAME
    original = path.read_bytes()
    diary.handle("TEXTE : After disk failure.")
    diary.acknowledge()
    with patch.object(
        storage.os, "replace", side_effect=OSError("private exception text")
    ):
        result = diary.handle("ENREGISTRER")
    assert "private exception" not in result["display"]
    assert path.read_bytes() == original
    assert not list(path.parent.glob(".journal-encrypted-*"))
    assert diary.days == {"2026-10-30": "Before disk failure."}


def test_review_append_replace_cancel_delete_and_latest_only(diary):
    save(diary, "Premier paragraphe.")
    save(diary, "Second paragraphe.")
    assert diary.days["2026-10-30"] == "Premier paragraphe.\n\nSecond paragraphe."
    diary.handle("MODIFIER raccourcis")
    diary.model.draft.assert_called_once_with(
        "MODIFIER raccourcis", diary.days["2026-10-30"]
    )
    assert "Relisez" in diary.handle("ENREGISTRER")["display"]
    diary.acknowledge()
    diary.handle("ENREGISTRER")
    assert diary.days["2026-10-30"] == "Texte rédigé, sans faits inventés."
    diary.handle("TEXTE : Never saved.")
    diary.handle("ANNULER")
    assert diary.draft is None
    diary.handle("EFFACER AUJOURD'HUI")
    diary.handle("LIRE")
    diary.handle("OUI")
    assert diary.days  # An intervening command invalidated deletion confirmation.
    diary.handle("EFFACER AUJOURD'HUI")
    diary.handle("OUI")
    assert diary.days == {}


def test_old_days_read_only_and_appending_while_viewing_past(diary):
    save(diary, "Old day.")
    diary.date = lambda: date(2026, 10, 31)
    assert "Old day." in diary.handle("LIRE 30/10/2026")["display"]
    assert "lecture seule" in diary.handle("MODIFIER 30/10/2026 change it")["display"]
    assert "Seule la journée actuelle" in diary.handle("EFFACER 30/10/2026")["display"]
    save(diary, "New day.")
    diary.close()
    diary.open()
    result = diary.handle("x")
    assert "New day." in result["display"] and "Old day." not in result["display"]
    assert diary.handle("LIRE 30/10/2026")["display"].startswith("2026-10-30")
    diary.model.draft.assert_not_called()
    diary.model.date.assert_not_called()


def test_midnight_requires_review_and_cancels_delete(diary):
    save(diary, "Old day.")
    diary.handle("TEXTE : Midnight draft.")
    diary.acknowledge()
    diary.handle("EFFACER AUJOURD'HUI")
    diary.date = lambda: date(2026, 10, 31)
    assert "Suppression annulée" in diary.handle("OUI")["display"]
    result = diary.handle("ENREGISTRER")
    assert result.get("review") and "date a changé" in result["display"]
    diary.acknowledge()
    diary.handle("ENREGISTRER")
    assert diary.days == {"2026-10-30": "Old day.", "2026-10-31": "Midnight draft."}


def test_password_confirmation_and_change_accepts_short_password(diary):
    save(diary, "Secret survives change.")
    assert diary.handle("MOT DE PASSE")["secret"]
    assert "incorrect" in diary.handle("wrong")["display"]
    diary.handle("x")
    diary.handle("z")
    assert "diffèrent" in diary.handle("Z")["display"]
    diary.handle("z")
    assert not diary.handle("z")["secret"]
    with pytest.raises(storage.JournalError):
        diary.vault.open("x")
    assert diary.vault.open("z")[2] == diary.days
    diary.model.draft.assert_not_called()


def test_concurrent_session_and_deleted_identity_cannot_overwrite(diary):
    save(diary, "Original.")
    second = journal.Session(diary.vault)
    try:
        second.open()
        second.handle("x")
        save(diary, "Concurrent change.")
        second.handle("TEXTE : Stale overwrite.")
        second.acknowledge()
        assert "modifié ailleurs" in second.handle("ENREGISTRER")["display"]
        user = diary.vault.user
        diary.vault.registry.delete(user.id, user.revision, user.display_name)
        assert diary.handle("LIRE")["closed"]
        assert diary.key is None and diary.days == {}
        fresh = diary.vault.registry.resolve(user.display_name)
        assert fresh.id != user.id
        assert not storage.Vault(fresh, diary.vault.registry).exists()
    finally:
        second.close()


def test_timeout_and_late_model_output_clear_keys(diary):
    def late(*args):
        diary.close()
        return "Should never be shown."

    diary.model.draft.side_effect = late
    result = diary.handle("Synthetic private notes.")
    assert result["closed"] and "Should never" not in result["display"]
    assert diary.key is None and diary.draft is None
    diary.open()
    diary.handle("x")
    diary.clock = lambda: diary.deadline + 1
    diary.expire(diary.epoch)
    assert diary.stage == "closed" and diary.days == {}


def test_storage_refuses_symlink_and_structure_injection(diary, tmp_path):
    assert not diary.handle("TEXTE : Hello\n## 2020-01-01\nInjected").get("review")
    outside = tmp_path / "outside"
    outside.write_bytes(b"untouched")
    path = diary.vault.store.root / diary.vault.user.memory_key / storage.FILENAME
    path.unlink()
    path.symlink_to(outside)
    with pytest.raises(storage.JournalError):
        diary.vault.open("x")
    assert outside.read_bytes() == b"untouched"


def test_stateless_model_has_only_supplied_text_and_sanitizes_errors(monkeypatch):
    response = Mock()
    response.json.return_value = {
        "choices": [{"finish_reason": "stop", "message": {"content": "Draft."}}]
    }
    post = Mock(return_value=response)
    monkeypatch.setattr(journal.requests, "post", post)
    model = journal.Model(lambda: ("synthetic-api-key", "mistral-small-latest"))
    assert model.draft("My notes", "Current paragraph") == "Draft."
    data = post.call_args.kwargs["json"]
    assert "tools" not in data
    assert len(data["messages"]) == 2
    assert json_data(data["messages"][1]["content"]) == {
        "instruction": "My notes",
        "previous_text": "Current paragraph",
    }
    response.raise_for_status.side_effect = journal.requests.HTTPError(
        "private remote payload"
    )
    with pytest.raises(journal.JournalError, match="indisponible") as error:
        model.draft("My notes", "")
    assert "private remote payload" not in str(error.value)


def json_data(text):
    import json

    return json.loads(text)
