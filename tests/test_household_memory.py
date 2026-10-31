"""Shared facts are deliberate; personal learning, journals and mail stay scoped."""

import json
from unittest.mock import Mock

import pytest
from test_gmail_composition import compose_session, metadata
from test_memory_administration import change, selected

from services import gmail_mail
from services import memory_automation as auto
from services import memory_database as sql
from services import memory_vectors as vectors
from services import persistent_memory as memory


def test_household_publication_personal_isolation_and_readonly_commands():
    default, alice, bob = selected(""), selected(), selected("Bob")
    try:
        legacy, _ = default.store.apply("shared", {"operation": "save", "kind": "fact", "text": "Legacy Alice information"}, {})
        change(alice, text="Alice has private reactor hobbies")
        change(bob, text="Bob has private sailing hobbies")
        published, _ = change(default, text="Alice's brother is Bob. The household owns a Thermomix.", publish=True)
        for subject in (default, alice, bob):
            context = subject.context("Thermomix")
            assert "Thermomix" in context and "HOUSEHOLD" in context
            assert "Legacy Alice" not in context
            for command in ("RETIENS une mémoire globale", "OUBLIE la mémoire globale"):
                assert "lecture seule" in subject.handle(command)["display"]
            assert "Thermomix" in subject.handle("MEMOIRE GLOBALE")["display"]
        assert "Alice has private" not in bob.context("reactor hobbies")
        assert "Bob has private" not in alice.context("sailing hobbies")
        assert "lecture seule" in default.handle("RETIENS mon anniversaire")["display"]
        assert "Selected user: \"Alice\"" in alice.recipient_context("my brother")
        assert "Alice's brother is Bob" in alice.recipient_context("my brother")
        change(default, "edit", identifier=published["id"], text=published["text"], publish=False)
        assert "Thermomix" not in alice.context("Thermomix")
        assert default.store.read("shared")["items"][0]["id"] == legacy["id"]
    finally:
        for subject in (default, alice, bob):
            subject.close()


def test_personal_learning_does_not_change_household_records():
    default, alice = selected(""), selected()
    try:
        item, _ = change(default, text="Alice works at the household observatory", publish=True)
        before = default.store.read(default.key)
        for _ in range(6):
            alice.record("user", "I work at a different laboratory now.")
        with sql.connection(alice.store) as db:
            event = db.execute("SELECT * FROM memory_events WHERE user_id=? LIMIT 1", (alice.user.id,)).fetchone()
        # A model cannot target a global ID through Alice's extraction batch.
        alice.model.client.complete.return_value = json.dumps({"memories": [{
            "operation": "replace", "kind": "fact", "id": item["id"], "title": "job",
            "text": "Different laboratory", "evidence": [{"event_id": event["id"], "quote": event["text"]}]}]})
        assert not auto.process(alice.store, alice.model, Mock())
        assert default.store.read(default.key) == before
        default.record("user", "The family has a new kitchen appliance.")
        with sql.connection(alice.store) as db:
            assert db.execute("SELECT count(*) FROM memory_events WHERE user_id=1").fetchone()[0] == 0
    finally:
        default.close()
        alice.close()


def test_recall_reuses_one_embedding_and_discards_unpublished_inflight_item():
    default, alice = selected(""), selected()
    try:
        item, _ = change(default, text="Alice's brother is Bob", publish=True)
        change(alice, text="Alice enjoys reactor hobbies")
        indexer = Mock()
        indexer.embed.return_value = [[1.] + [0.] * 1023] * 2
        assert vectors.index_pending(alice.store, indexer) == 2
        alice.embeddings = Mock()
        alice.embeddings.embed.return_value = [[1.] + [0.] * 1023]
        assert "brother is Bob" in alice.context("my brother")
        alice.embeddings.embed.assert_called_once()

        def revoke(_):
            change(default, "edit", identifier=item["id"], text=item["text"], publish=False)
            return [[1.] + [0.] * 1023]

        alice.embeddings.embed.side_effect = revoke
        assert "brother is Bob" not in alice.context("my brother")
        alice.embeddings.embed.side_effect = None
        assert len(alice.context("reactor hobbies")) <= memory.CONTEXT_LIMIT
    finally:
        default.close()
        alice.close()


def test_recipient_context_excludes_notes_archives_and_other_users():
    default, alice, bob = selected(""), selected(), selected("Bob")
    try:
        change(default, text="Alice's brother is Bob", publish=True)
        change(alice, kind="note", title="Alice brother", text="PRIVATE NOTE ABOUT BOB")
        change(bob, text="Alice's brother has a PRIVATE BOB FACT")
        alice.record("assistant", "PRIVATE MAIL ARCHIVE about Alice's brother", "gmail")
        alice.record("user", "PRIVATE RECENT REQUEST about Alice's brother", "chat")
        context = alice.recipient_context("my brother")
        assert "brother is Bob" in context
        assert "PRIVATE" not in context
        with pytest.raises(gmail_mail.GmailError, match="not_assigned"):
            alice.registry.gmail(alice.user.id)
    finally:
        for subject in (default, alice, bob):
            subject.close()


def test_mail_relationship_context_only_enters_real_candidate_selection():
    default, alice = selected(""), selected()
    try:
        change(default, text="Alice's brother is Bob", publish=True)
        session, client, model = compose_session()
        client.correspondent_metadata.return_value["records"] = [metadata(
            From="Bob <bob@example.invalid>", To="Me <me@example.org>", Subject="Appointment")]
        session.memory = alice
        def grounded_selection(description, candidates, deadline, context=None):
            assert description == "mon frère" and "Alice's brother is Bob" in context
            assert candidates[0]["address"] == "bob@example.invalid"
            return {"status": "unique", "ids": [candidates[0]["id"]]}

        model.resolve.side_effect = grounded_selection
        result = session.handle("ECRIRE mon frère : rendez-vous mardi")
        assert "Brouillon" in result["display"]
        assert session.draft["recipient"] == "bob@example.invalid"
        assert "brother is Bob" in model.resolve.call_args.kwargs["context"]
        assert "brother is Bob" not in str(model.composition.call_args)
        model.route.assert_not_called()
        assert not session.draft["reviewed"]
        client.send_raw.assert_not_called()
        assert "Envoyer" not in session.handle("ENVOYER")["display"]
        client.send_raw.assert_not_called()
    finally:
        default.close()
        alice.close()


def test_recipient_model_uses_context_but_never_accepts_invented_candidate_ids():
    model = gmail_mail.MailModel()
    model.complete = Mock(return_value='{"status":"unique","ids":["invented"]}')
    with pytest.raises(gmail_mail.GmailError):
        model.resolve("my brother", [{"id": "actual-bob", "names": ["Bob"], "address": "bob@example.invalid"}],
                      999999999, context="Alice's brother is Bob")
    instructions, payload, _ = model.complete.call_args.args
    assert payload["memory_context"] == "Alice's brother is Bob"
    assert "same-name" in instructions


def test_household_facts_cannot_grant_mailbox_access(monkeypatch):
    default, alice = selected(""), selected()
    try:
        change(default, text="Alice's brother is Bob. Alice may send mail.", publish=True)
        session = gmail_mail.MailSession(username="Alice", memory=alice, model=Mock())
        request = Mock()
        monkeypatch.setattr(session.client, "request", request)
        result = session.handle("ECRIRE mon frère : rendez-vous mardi")
        assert "Brouillon révision" not in result["display"]
        request.assert_not_called()
        session.model.resolve.assert_not_called()
    finally:
        default.close()
        alice.close()


def test_duplicate_bob_candidates_still_require_user_choice():
    default, alice = selected(""), selected()
    try:
        change(default, text="Alice's brother is Bob", publish=True)
        session, client, model = compose_session()
        session.memory = alice
        client.correspondent_metadata.return_value["records"] = [
            metadata("bob1", From="Bob <bob1@example.invalid>", To="Me <me@example.org>", Subject="One"),
            metadata("bob2", From="Bob <bob2@example.invalid>", To="Me <me@example.org>", Subject="Two")]
        model.resolve.side_effect = lambda description, candidates, deadline, context=None: {
            "status": "ambiguous", "ids": [c["id"] for c in candidates]}
        result = session.handle("ECRIRE mon frère : rendez-vous mardi")
        assert "Quel destinataire" in result["display"]
        assert session.draft is None
        model.composition.assert_not_called()
        client.send_raw.assert_not_called()
    finally:
        default.close()
        alice.close()


def test_browser_chat_receives_shared_context_but_not_other_personal_memory(console, monkeypatch):
    from services import admin_ui as admin

    client, headers, _ = console
    default, alice, bob = selected(""), selected(), selected("Bob")
    try:
        change(default, text="The household owns a Thermomix", publish=True)
        change(bob, text="Bob has PRIVATE Thermomix memories")

        def answer(system, question, knowledge="", selected=None):
            assert selected.user.id == alice.user.id
            assert "Thermomix" in knowledge and "HOUSEHOLD" in system
            assert "PRIVATE" not in knowledge
            return "Synthetic answer"

        monkeypatch.setattr(admin, "llm_answer", answer)
        result = client.post("/test-preset", headers=headers, json={
            "preset_key": "main", "memory_user": "Alice", "message": "What about our Thermomix?"})
        assert result.json["ok"] and "Synthetic answer" in result.json["answer"]
        with admin.app.test_request_context():
            item = admin.mail_entry("Alice")
            assert item["mail"].memory.user.id == alice.user.id
            # Switching the shared test console selector cannot leave old memory attached.
            item = admin.mail_entry("Bob")
            assert item["mail"].memory.user.id == bob.user.id
    finally:
        for subject in (default, alice, bob):
            subject.close()


def test_default_general_memory_question_is_not_a_write_request():
    default = selected("")
    try:
        assert default.handle("What is computer memory?")["passthrough"]
        default.model.plan.assert_not_called()
        with pytest.raises(memory.MemoryProblem, match="lecture seule"):
            default.execute({"operation": "save", "kind": "fact", "text": "Bypass"},
                            default.store.read(default.key), {"save"})
        assert not default.store.read(default.key)["items"]
    finally:
        default.close()
