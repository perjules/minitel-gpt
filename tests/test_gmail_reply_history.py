"""Reply-only history is attached after approval, using synthetic MIME and accounts."""
from email import policy
from email.parser import BytesParser

import pytest
from test_gmail import body as message_body
from test_gmail import draft, make_session, message

from services import gmail_client as api
from services import gmail_mail as mail


def sent_message(client):
    return BytesParser(policy=policy.default).parsebytes(client.send.call_args.args[0])


@pytest.mark.parametrize("html", [False, True])
def test_sent_reply_quotes_full_selected_body_with_existing_history_only_after_review(html):
    source = "Échange récent\n\n" + "texte déjà reçu " * 1000 + "\n> Ancienne réponse\nFIN HISTORIQUE"
    session, client, model = make_session(text=source, output="Merci, c'est confirmé.")
    session.auto_summary_chars = 0
    original = message(text=source)
    original["payload"]["headers"][0]["value"] = "Élodie <owner@example.org>"
    if html:
        original["payload"] = {
            **original["payload"], "mimeType": "text/html",
            "body": message_body(
                "<p>" + source.replace("\n", "<br>") + "</p>", "text/html"
            )["body"],
        }
    client.read.return_value = api.message_record(original, full=True)
    session.handle("MAIL")
    session.handle("LIRE 1")
    result = session.handle("REPONDRE confirmer")
    ticket = result["review_ticket"]
    stored = session.draft
    approved_body = stored["body"]
    reviewed_mime = stored["raw"]
    assert "FIN HISTORIQUE" not in result["display"]
    assert "FIN HISTORIQUE" not in BytesParser(policy=policy.default).parsebytes(reviewed_mime).get_content()
    assert "FIN HISTORIQUE" not in model.complete.call_args.args[1]["email"]
    session.handle("ENVOYER", ticket)
    client.send.assert_not_called()
    session.acknowledge(ticket)
    calls = model.method_calls[:]
    session.handle("ENVOYER", ticket)
    client.send.assert_called_once()
    sent = sent_message(client)
    text = sent.get_content().replace("\r\n", "\n")
    assert text.startswith(approved_body + "\n\nLe ")
    assert "Élodie <owner@example.org> a écrit :\n" in text
    assert "> Échange récent" in text
    assert "> > Ancienne réponse\n> FIN HISTORIQUE" in text
    assert "<br>" not in text
    assert sent["Subject"] == "Re: Test"
    assert sent["In-Reply-To"] == "<original@example.org>"
    assert "<older@example.org>" in sent["References"]
    assert sent["Message-ID"] == stored["message_id"]
    assert client.send.call_args.args[1] == "thread123"
    assert model.method_calls == calls
    assert client.read.call_count == 1
    assert stored["body"] == approved_body and stored["raw"] == reviewed_mime
    assert session.draft is None
    session.handle("ENVOYER", ticket)
    client.send.assert_called_once()


def test_literal_revision_retains_bound_source_and_quotes_it_once_beneath_final_reply():
    session, client, _ = make_session(text="ORIGINAL HISTORY", output="Initial reply")
    old = draft(session)
    session.acknowledge(old)
    session.selected["body"] = "DIFFERENT HISTORY"
    session.handle("MODIFIER")
    revised = session.handle("R: Final reply")["review_ticket"]
    session.handle("ENVOYER", old)
    client.send.assert_not_called()
    session.acknowledge(revised)
    session.handle("ENVOYER", revised)
    text = sent_message(client).get_content().replace("\r\n", "\n")
    assert text.startswith("Final reply\n\n" + mail.SIGNATURE + "\n\nLe ")
    assert "Initial reply" not in text and "DIFFERENT HISTORY" not in text
    assert text.count("> ORIGINAL HISTORY") == 1


def test_new_message_after_reading_an_email_never_appends_its_history():
    session, client, model = make_session(text="PRIVATE SELECTED HISTORY")
    session.handle("MAIL")
    session.handle("LIRE 1")
    model.composition.return_value = ("New subject", "New message")
    ticket = session.handle("ECRIRE new@example.net : bonjour")["review_ticket"]
    original_raw = session.draft["raw"]
    session.acknowledge(ticket)
    session.handle("ENVOYER", ticket)
    assert client.send.call_args.args[0] == original_raw
    assert client.send.call_args.args[1] is None
    sent = sent_message(client)
    assert "PRIVATE SELECTED HISTORY" not in sent.get_content()
    assert sent["In-Reply-To"] is None and sent["References"] is None
