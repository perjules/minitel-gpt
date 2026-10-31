"""First conversations with synthetic users, structured replies and real SQLite."""

import json
import time
from unittest.mock import Mock

import pytest

from services import memory_database as sql
from services import memory_session as sessions
from services.gmail_auth import GmailError


@pytest.fixture
def newcomer(tmp_path, monkeypatch):
    monkeypatch.setenv("MINITEL_ONBOARDING", "1")
    selected = sessions.MemorySession("Camille", model=Mock(), root=tmp_path)
    yield selected
    selected.close()


def response(reply="Quel âge avez-vous ?", topic="age", **changes):
    data = {
        "reply": reply,
        "action": "continue",
        "asked_topic": topic,
        "covered": [],
        "skipped": [],
        "memories": [],
    }
    data.update(changes)
    return json.dumps(data, ensure_ascii=False)


def run(selected, question=None, **changes):
    selected.model.client.complete.return_value = response(**changes)
    return selected.welcome.respond(
        question, "OWNER PERSONA unchanged", start=question is None
    )


def fact(quote, text="User is 10 years old.", event_id=1):
    return {
        "topic": "age",
        "operation": "save",
        "kind": "fact",
        "title": "age",
        "text": text,
        "id": None,
        "evidence": [{"event_id": event_id, "quote": quote}],
    }


def test_only_new_named_profiles_enrolled_and_existing_can_opt_in(newcomer):
    assert newcomer.welcome.should_greet()
    default = sessions.MemorySession("", root=newcomer.store.root)
    try:
        assert not default.welcome.should_greet()
        assert default.welcome.respond("FAISONS CONNAISSANCE", "persona") is None
    finally:
        default.close()
    with newcomer.registry.connection() as db:
        db.execute("DELETE FROM user_onboarding WHERE user_id=?", (newcomer.user.id,))
    assert not newcomer.welcome.should_greet()
    newcomer.model.client.complete.return_value = response()
    assert newcomer.welcome.respond("FAISONS CONNAISSANCE", "persona")["onboarding"]


def test_intro_uses_owner_persona_then_short_age_answer_has_question_provenance(
    newcomer,
):
    intro = run(newcomer)
    assert intro["display"] == "Quel âge avez-vous ?"
    args = newcomer.model.client.complete.call_args.args
    assert args[0].startswith("OWNER PERSONA unchanged")
    assert "immense vintage mainframe" in args[0] and "NEVER employment" in args[0]
    assert args[1]["phase"] == "first" and args[1]["username"] == "Camille"
    result = run(
        newcomer,
        "10",
        reply="Quelle matière aimez-vous à l’école ?",
        topic="daily_life",
        covered=["age"],
        memories=[fact("10")],
    )
    assert "école" in result["display"]
    item = newcomer.store.read(newcomer.key)["items"][0]
    assert item["text"] == "User is 10 years old." and item["origin"] == "onboarding"
    assert (
        item["source"][0]["quote"] == "10"
        and item["source"][0]["question"] == intro["display"]
    )
    assert newcomer.welcome.state()["progress"]["age"] == "covered"
    assert not list(newcomer.store.root.glob("*.json"))
    with sql.connection(newcomer.store) as db:
        assert db.execute("SELECT count(*) FROM memory_events").fetchone()[0] == 0


def test_skip_is_sticky_and_cannot_be_reasked_even_after_restart(newcomer):
    run(newcomer, topic="household", reply="Avec qui vivez-vous ?")
    run(
        newcomer,
        "PASSER",
        topic="interests",
        reply="Qu’aimez-vous faire pour vous amuser ?",
    )
    newcomer.close()
    again = sessions.MemorySession("CAMILLE", root=newcomer.store.root, model=Mock())
    try:
        assert again.welcome.state()["progress"]["household"] == "skipped"
        run(
            again,
            "Le dessin.",
            topic="household",
            reply="Parlez-moi de votre famille ?",
        )
        assert again.welcome.state()["status"] == "paused"
        assert not again.store.read(again.key)["items"]
    finally:
        again.close()


def test_natural_refusal_and_user_led_followup_are_tracked(newcomer):
    run(newcomer, topic="household", reply="Souhaitez-vous parler de votre famille ?")
    result = run(
        newcomer,
        "Je préfère ne pas parler de ma famille.",
        skipped=["household"],
        topic="interests",
        reply="Quel loisir vous plaît ?",
    )
    assert (
        result["onboarding"]
        and newcomer.welcome.state()["progress"]["household"] == "skipped"
    )
    run(
        newcomer,
        "Je dessine des fusées et pourrais en parler des heures.",
        covered=["interests"],
        topic="interests",
        reply="Qu’est-ce qui vous plaît dans le dessin de fusées ?",
    )
    assert newcomer.welcome.state()["asked"] == "interests"
    assert "follow their enthusiasm" in newcomer.model.client.complete.call_args.args[0]


def test_pause_stop_resume_and_completion_are_not_repeated(newcomer):
    run(newcomer)
    newcomer.model.client.complete.reset_mock()
    result = newcomer.welcome.respond("PLUS TARD", "persona")
    assert result["onboarding"] and not newcomer.welcome.should_greet()
    newcomer.model.client.complete.assert_not_called()
    assert newcomer.welcome.respond("Bonjour", "persona") is None
    newcomer.model.client.complete.return_value = response()
    newcomer.welcome.respond("REPRENDRE ACCUEIL", "persona")
    assert newcomer.model.client.complete.call_args.args[1]["phase"] == "resume"
    newcomer.welcome.respond("FIN ACCUEIL", "persona")
    assert newcomer.welcome.state()["status"] == "skipped"
    assert not newcomer.welcome.state()["history"]
    newcomer.model.client.complete.return_value = response(
        reply="Merci, mes banques sont à votre service.",
        action="finish",
        asked_topic=None,
    )
    newcomer.welcome.respond("REPRENDRE ACCUEIL", "persona")
    assert newcomer.welcome.state()["status"] == "complete"
    assert not newcomer.welcome.should_greet()


@pytest.mark.parametrize(
    "changes",
    [
        {"asked_topic": "unrecognised"},
        {"owner": 999},
    ],
)
def test_invalid_model_output_never_partially_writes(newcomer, changes):
    run(newcomer)
    newcomer.model.client.complete.return_value = response(**changes)
    newcomer.welcome.respond("10", "persona")
    assert not newcomer.store.read(newcomer.key)["items"]
    assert newcomer.welcome.state()["status"] == "paused"


@pytest.mark.parametrize("memories", [[fact("invention")], [fact("10", event_id=99)],
                                      [{"path": "/tmp/file"}], None])
def test_invalid_memory_does_not_break_a_valid_conversation(newcomer, caplog, memories):
    run(newcomer)
    result = run(newcomer, "10", topic="daily_life", reply="Quelle matière aimez-vous ?",
                 memories=memories)
    assert result["display"] == "Quelle matière aimez-vous ?"
    assert newcomer.welcome.state()["status"] == "active"
    assert not newcomer.store.read(newcomer.key)["items"]
    assert "onboarding status=memory_rejected" in caplog.text
    assert "10" not in caplog.text and "/tmp/file" not in caplog.text


def test_safe_save_defaults_and_conversational_punctuation_are_not_fatal(newcomer):
    run(newcomer)
    proposal = fact("10")
    del proposal["id"], proposal["title"]
    result = run(newcomer, "10", topic="daily_life", memories=[proposal],
                 reply="Qu'aimez-vous à l'école ? Ou voulez-vous m'en dire plus ?")
    assert "école" in result["display"]
    assert newcomer.welcome.state()["status"] == "active"
    assert newcomer.store.read(newcomer.key)["items"][0]["text"] == "User is 10 years old."


def test_no_personal_facts_without_user_reply_and_no_save_refusal(newcomer):
    run(newcomer, memories=[fact("Invented biography")])
    assert not newcomer.store.read(newcomer.key)["items"]
    newcomer.model.client.complete.return_value = response()
    newcomer.welcome.respond("REPRENDRE ACCUEIL", "persona")
    newcomer.model.client.complete.return_value = response(
        reply="D’accord. Quel loisir vous plaît ?", topic="interests"
    )
    newcomer.welcome.respond("J’ai 10 ans, ne le mémorise pas.", "persona")
    args = newcomer.model.client.complete.call_args.args[1]
    assert args["current_reply"] == "PASSER"
    assert "10 ans" not in str(args)
    assert not newcomer.store.read(newcomer.key)["items"]


def test_explicit_forget_and_close_during_call_invalidate_late_writes(newcomer):
    run(newcomer)
    item, _ = newcomer.store.apply(
        newcomer.key, {"operation": "save", "kind": "fact", "text": "Old fact."}, {}
    )
    # This explicit write clears older welcome history before the next model call.
    assert newcomer.welcome.state()["history"] == []

    def late(*args, **kwargs):
        newcomer.store.apply(
            newcomer.key,
            {"operation": "remove", "ids": [item["id"]]},
            newcomer.store.read(newcomer.key),
        )
        return response(memories=[fact("I like rockets.", text="Likes rockets.")])

    newcomer.model.client.complete.side_effect = late
    newcomer.welcome.respond("I like rockets.", "persona")
    assert not newcomer.store.read(newcomer.key)["items"]
    assert newcomer.welcome.state()["history"] == []
    newcomer.model.client.complete.side_effect = lambda *args, **kwargs: (
        newcomer.close() or response()
    )
    newcomer.welcome.respond("REPRENDRE ACCUEIL", "persona")
    with newcomer.registry.connection() as db:
        assert (
            db.execute(
                "SELECT status FROM user_onboarding WHERE user_id=?",
                (newcomer.user.id,),
            ).fetchone()[0]
            == "paused"
        )


def test_api_failure_pauses_without_blocking_ordinary_chat(newcomer):
    newcomer.model.client.complete.side_effect = GmailError("PRIVATE PROVIDER RESPONSE")
    result = newcomer.welcome.respond(None, "persona", start=True)
    assert "PRIVATE" not in result["display"]
    assert not newcomer.welcome.should_greet()
    assert newcomer.welcome.respond("Hello", "persona") is None


def test_other_user_isolated_and_version_mismatch_preserves_state(newcomer):
    run(newcomer)
    other = sessions.MemorySession("Bob", root=newcomer.store.root, model=Mock())
    try:
        assert other.welcome.state()["history"] == []
        with other.registry.connection() as db:
            db.execute(
                "UPDATE user_onboarding SET version=999 WHERE user_id=?",
                (other.user.id,),
            )
        assert not other.welcome.should_greet()
        assert other.welcome.respond("Hello", "persona") is None
        assert newcomer.welcome.state()["history"]
    finally:
        other.close()


def test_skipped_topics_cannot_receive_new_facts_even_if_model_proposes_them(newcomer):
    run(newcomer)
    run(newcomer, "PASSER", reply="Quel loisir vous plaît ?", topic="interests")
    result = run(
        newcomer,
        "I like drawing.",
        reply="Que dessinez-vous ?",
        topic="interests",
        memories=[fact("I like drawing.", text="Unsupported age detail.")],
    )
    assert result["onboarding"] and newcomer.welcome.state()["status"] == "active"
    assert not newcomer.store.read(newcomer.key)["items"]


def test_natural_short_skip_is_applied_before_the_model_call(newcomer):
    run(newcomer, topic="household", reply="Avec qui vivez-vous ?")
    run(
        newcomer,
        "Je préfère ne pas répondre.",
        topic="interests",
        reply="Quel loisir vous plaît ?",
    )
    data = newcomer.model.client.complete.call_args.args[1]
    assert data["progress"]["household"] == "skipped"
    assert not newcomer.store.read(newcomer.key)["items"]


def test_concurrent_welcome_cannot_overwrite_the_newer_answer(newcomer):
    run(newcomer)
    other = sessions.MemorySession("CAMILLE", root=newcomer.store.root, model=Mock())
    try:

        def delayed(*args, **kwargs):
            run(
                other,
                "10",
                topic="daily_life",
                reply="Quelle matière aimez-vous ?",
                memories=[fact("10")],
            )
            return response(
                topic="daily_life",
                reply="Quel cours préférez-vous ?",
                memories=[fact("11", text="User is 11 years old.")],
            )

        newcomer.model.client.complete.side_effect = delayed
        newcomer.welcome.respond("11", "persona")
        items = newcomer.store.read(newcomer.key)["items"]
        assert len(items) == 1 and items[0]["text"] == "User is 10 years old."
        assert other.welcome.state()["status"] == "active"
    finally:
        other.close()


def test_english_control_and_model_language(newcomer):
    newcomer.model.client.complete.return_value = response(reply="How old are you ?")
    newcomer.welcome.respond(None, "persona", language="en", start=True)
    assert newcomer.model.client.complete.call_args.args[0].endswith("Speak English.")
    assert (
        "As you wish"
        in newcomer.welcome.respond("LATER", "persona", language="en")["display"]
    )


def test_actual_http_prompt_preserves_character_only_for_welcome(newcomer, monkeypatch):
    from services import gmail_mail

    post = Mock()
    post.return_value.json.return_value = {
        "choices": [{"finish_reason": "stop", "message": {"content": response()}}]
    }
    monkeypatch.setattr(gmail_mail.requests, "post", post)
    newcomer.model.client = gmail_mail.MailModel(
        settings=lambda: ("synthetic-key", "synthetic-model"), service="general"
    )
    assert newcomer.welcome.respond(None, "OWNER PERSONA", start=True)["onboarding"]
    instructions = post.call_args.kwargs["json"]["messages"][0]["content"]
    assert instructions.startswith("OWNER PERSONA")
    assert "no fictional banter" not in instructions
    assert "real capabilities must remain truthful" in instructions
    assert "tools" not in post.call_args.kwargs["json"]
    newcomer.model.client.complete(
        "Factual extraction", {}, time.monotonic() + 5
    )
    assert (
        "no fictional banter" in post.call_args.kwargs["json"]["messages"][0]["content"]
    )


@pytest.mark.parametrize('command', ["Passons à autre chose !", "Changeons de sujet",
                                    "Parlons d’autre chose", "Let's move on"])
def test_explicit_topic_change_cannot_be_ignored_or_memorized(newcomer, command):
    run(newcomer, topic='interests', reply='Que dessinez-vous ?')
    result = run(newcomer, command, topic='interests', reply='Parlez-moi encore du dessin ?')
    sent = newcomer.model.client.complete.call_args.args[1]
    assert sent['change_subject'] is True
    assert sent['progress']['interests'] == 'covered'
    assert 'dessin' not in result['display']
    assert newcomer.welcome.state()['asked'] == 'age'
    assert not newcomer.store.read(newcomer.key)['items']


def test_model_can_choose_a_new_uncovered_subject_naturally(newcomer):
    run(newcomer, topic='interests', reply='Que dessinez-vous ?')
    result = run(newcomer, 'Passons à autre chose', topic='household',
                 reply='D’accord. Avec qui aimez-vous passer du temps ?')
    assert result['display'] == 'D’accord. Avec qui aimez-vous passer du temps ?'
    assert newcomer.welcome.state()['asked'] == 'household'


def test_move_on_finishes_instead_of_looping_when_no_topics_remain(newcomer):
    from services import onboarding
    run(newcomer, topic='interests', reply='Que dessinez-vous ?')
    with newcomer.registry.connection() as db:
        db.execute('UPDATE user_onboarding SET progress=? WHERE user_id=?',
                   (json.dumps({topic: 'covered' for topic in onboarding.TOPICS}), newcomer.user.id))
    result = run(newcomer, 'passons à autre chose', topic='interests', reply='Encore du dessin ?')
    assert result['onboarding'] and newcomer.welcome.state()['status'] == 'complete'
    assert newcomer.welcome.respond('Bonjour', 'persona') is None


def test_ui_pause_fences_inflight_reply_and_persists_until_explicit_resume(newcomer):
    run(newcomer)

    def delayed(*args, **kwargs):
        newcomer.welcome.pause()
        return response(memories=[fact('10')])

    newcomer.model.client.complete.side_effect = delayed
    newcomer.welcome.respond('10', 'persona')
    assert newcomer.welcome.state()['status'] == 'paused'
    assert not newcomer.store.read(newcomer.key)['items']
    assert newcomer.welcome.respond('Bonjour', 'persona') is None
    newcomer.close()
    again = sessions.MemorySession('Camille', root=newcomer.store.root, model=Mock())
    try:
        assert not again.welcome.should_greet()
        again.model.client.complete.return_value = response()
        again.welcome.respond('REPRENDRE ACCUEIL', 'persona')
        assert again.welcome.state()['status'] == 'active'
    finally:
        again.close()
