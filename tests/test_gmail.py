"""Offline Gmail contracts. Fake APIs only: these tests cannot send real email."""

import base64
import importlib.util
import io
import json
import tempfile
import threading
import time
import unittest
from email import policy
from email.parser import BytesParser
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests
from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from services import gmail_auth as auth
from services import gmail_client as api
from services import gmail_mail as mail


def body(value, kind="text/plain", **extra):
    return {
        "mimeType": kind,
        "body": {
            "data": base64.urlsafe_b64encode(value.encode()).decode(),
            "size": len(value.encode()),
        },
        **extra,
    }


def message(identifier="abc123", text="Bonjour", headers=None):
    payload = body(text)
    payload["headers"] = [
        {"name": k, "value": v}
        for k, v in (
            headers
            or {
                "From": "Owner <owner@example.org>",
                "Reply-To": "reply@example.org",
                "Subject": "Test",
                "Message-ID": "<original@example.org>",
                "References": "<older@example.org>",
            }
        ).items()
    ]
    return {
        "id": identifier,
        "threadId": "thread123",
        "internalDate": "1791806400000",
        "labelIds": ["INBOX", "UNREAD"],
        "payload": payload,
    }


def info():
    return {
        "client_id": "test.apps.googleusercontent.com",
        "client_secret": "fake-secret",
        "refresh_token": "fake-refresh",
        "token_uri": auth.TOKEN_URI,
        "scopes": list(auth.SCOPES),
        "email_address": "me@example.org",
    }


def make_session(text="Bonjour", output="Merci pour votre message."):
    client = Mock()
    record = api.message_record(message(text=text), full=True)
    client.list.return_value = [api.message_record(message(text=text))]
    client.read.return_value = record
    client.tokens.email_address = "me@example.org"
    client.send.return_value = "sent"
    model = Mock()
    model.complete.return_value = output
    model.route.return_value = {"action": "revise"}
    session = mail.MailSession(client, model)
    return session, client, model


def draft(session):
    session.handle("MAIL")
    session.handle("LIRE 1")
    return session.handle("REPONDRE remercier")["review_ticket"]


class MimeTests(unittest.TestCase):
    def test_prefers_plain_without_decoding_or_fetching_html(self):
        html = body("bad", "text/html")
        html["body"] = {"size": api.MAX_BODY + 1, "attachmentId": "never-fetch"}
        part = {"mimeType": "multipart/alternative", "parts": [html, body("plain")]}
        loader = Mock()
        self.assertEqual(api.mime_content(part, loader), ("plain", []))
        loader.assert_not_called()

    def test_html_is_inert_and_attachment_names_only(self):
        attachment = body("SECRET", "application/pdf", filename="report.pdf")
        text, files = api.mime_content(
            {
                "mimeType": "multipart/mixed",
                "parts": [
                    body(
                        "<style>hide</style><p>Hello &amp; welcome</p><script>evil()</script>"
                        '<img src="https://tracker.invalid/pixel"><a href="https://example.org">Link</a>',
                        "text/html",
                    ),
                    attachment,
                ],
            }
        )
        self.assertIn("Hello & welcome", text)
        self.assertIn("https://example.org", text)
        self.assertNotIn("evil", text)
        self.assertNotIn("tracker", text)
        self.assertNotIn("SECRET", text)
        self.assertEqual(files, [{"name": "report.pdf", "type": "application/pdf"}])

    def test_headers_charset_and_size(self):
        data = message(
            headers={
                "From": "=?UTF-8?B?SsOpYW4=?= <jean@example.org>",
                "Subject": "=?UTF-8?Q?=C3=89cole?=",
            }
        )
        record = api.message_record(data, True)
        self.assertIn("Jéan", record["sender"])
        self.assertEqual(record["subject"], "École")
        self.assertTrue(record["unread"])
        self.assertIn("2026", record["date"])
        part = body(
            "",
            headers=[
                {"name": "Content-Type", "value": "text/plain; charset=iso-8859-1"}
            ],
        )
        part["body"]["data"] = base64.urlsafe_b64encode("été".encode("latin1")).decode()
        self.assertEqual(api.mime_content(part)[0], "été")
        with self.assertRaisesRegex(auth.GmailError, "message_too_large"):
            api.mime_content(body("x" * (api.MAX_BODY + 1)))

    def test_recipient_replyto_preference_ambiguity_and_invalid_no_fallback(self):
        data = message()
        self.assertEqual(api.reply_addresses(data), ["reply@example.org"])
        data["payload"]["headers"] = [{"name": "From", "value": "from@example.org"}]
        self.assertEqual(api.reply_addresses(data), ["from@example.org"])
        self.assertEqual(
            api.addresses("a@example.org, b@example.org"),
            ["a@example.org", "b@example.org"],
        )
        self.assertEqual(len(api.addresses("a@example.org, a@example.org")), 2)
        for value in (
            "no-address",
            "bad@example.org\r\nBcc: thief@example.org",
            "A <broken>",
        ):
            with self.assertRaises(auth.GmailError):
                api.reply_addresses(
                    message(headers={"Reply-To": value, "From": "valid@example.org"})
                )


class WorkflowTests(unittest.TestCase):
    def test_number_binds_id_and_full_pagination_is_cached(self):
        session, client, _model = make_session("long " * 500)
        session.handle("MAIL")
        result = session.handle("LIRE 1")
        self.assertIn("Résumé IA", result["display"])
        self.assertEqual(client.read.call_args.args[0], "abc123")
        for page in mail.pages(session.handle("TEXTE")["display"]):
            self.assertTrue(all(len(line) <= 39 for line in page.splitlines()))
        session.handle("LIRE 1")
        client.read.assert_called_once()
        self.assertIn("Choisissez", session.handle("LIRE 9")["display"])
        self.assertEqual(client.list.call_count, 1)

    def test_draft_signature_threading_full_review_then_one_send(self):
        session, client, model = make_session(output="Merci.\n\n" + mail.SIGNATURE)
        ticket = draft(session)
        stored = session.draft
        self.assertEqual(stored["body"].count(mail.SIGNATURE), 1)
        mime = BytesParser(policy=policy.default).parsebytes(stored["raw"])
        self.assertEqual(mime["To"], "reply@example.org")
        self.assertEqual(mime["From"], "me@example.org")
        self.assertEqual(mime["Subject"], "Re: Test")
        self.assertEqual(mime["In-Reply-To"], "<original@example.org>")
        self.assertIn("<older@example.org>", mime["References"])
        self.assertIn(mail.SIGNATURE, mime.get_content())
        self.assertIn(
            "review_current_draft_first", session.handle("ENVOYER", ticket)["display"]
        )
        session.acknowledge(ticket)
        calls = model.complete.call_count
        self.assertIn("confirme", session.handle("ENVOYER", ticket)["display"])
        client.send.assert_called_once_with(
            stored["raw"], "thread123", stored["message_id"], unittest.mock.ANY
        )
        self.assertEqual(model.complete.call_count, calls)
        self.assertIsNone(session.draft)
        self.assertIn(
            "review_current_draft_first", session.handle("ENVOYER", ticket)["display"]
        )
        client.send.assert_called_once()

    def test_modify_then_personal_signoff_revises_reply_without_reclassification(self):
        session, client, _ = make_session()
        ticket = draft(session)
        previous = dict(session.draft)
        session.acknowledge(ticket)
        session.model = mail.MailModel()
        body = previous["body"].removesuffix("\n\n" + mail.SIGNATURE)
        with (
            patch.object(session.model, "route", side_effect=AssertionError("Edit was reclassified")),
            patch.object(session.model, "complete", return_value=body + "\n\nCamille") as complete,
        ):
            session.handle("MODIFIER")
            result = session.handle("Signe avec Camille")
        complete.assert_called_once()
        prompt, data = complete.call_args.args[:2]
        self.assertEqual(data["task"], "revise")
        self.assertEqual(data["instruction"], "Signe avec Camille")
        self.assertEqual(data["existing_reply"], body)
        self.assertIn("sender's closing name", prompt)
        self.assertEqual(session.draft["recipient"], previous["recipient"])
        self.assertEqual(session.draft["subject"], previous["subject"])
        self.assertEqual(session.draft["thread"], previous["thread"])
        self.assertEqual(session.draft["body"], body + "\n\nCamille\n\n" + mail.SIGNATURE)
        self.assertNotEqual(result["review_ticket"], ticket)
        self.assertFalse(session.review_valid(ticket))
        self.assertFalse(session.review_valid(result["review_ticket"]))
        client.send.assert_not_called()

    def test_revision_old_ticket_cannot_approve_and_ordinary_words_cannot_send(self):
        session, client, model = make_session()
        old = draft(session)
        session.acknowledge(old)
        revised = session.handle("Make it shorter")["review_ticket"]
        self.assertNotEqual(old, revised)
        session.acknowledge(old)
        self.assertFalse(session.review_valid(revised))
        self.assertEqual(session.draft["revision"], 2)
        self.assertIn("review_current", session.handle("ENVOYER", old)["display"])
        self.assertEqual(client.send.call_count, 0)
        session.acknowledge(revised)
        session.handle("MODIFIER")
        self.assertFalse(session.review_valid(revised))
        model.complete.side_effect = auth.GmailError("generation_unavailable")
        session.handle("shorter again")
        self.assertFalse(session.review_valid(revised))
        client.send.assert_not_called()

    def test_cancel_and_reset_discard_all_private_memory(self):
        for command in ("ANNULER", "SOMMAIRE"):
            session, client, _ = make_session()
            ticket = draft(session)
            session.acknowledge(ticket)
            session.handle(command)
            self.assertIsNone(session.draft)
            self.assertIsNone(session.selected)
            self.assertEqual(session.loaded, {})
            self.assertEqual(session.items, [])
            session.handle("ENVOYER", ticket)
            client.send.assert_not_called()

    def test_ambiguous_recipient_requires_choice_from_actual_headers(self):
        session, client, _ = make_session()
        client.read.return_value = api.message_record(
            message(
                headers={
                    "From": "from@example.org",
                    "Reply-To": "a@example.org, b@example.org",
                    "Subject": "Question",
                    "Message-ID": "<original@example.org>",
                }
            ),
            True,
        )
        session.handle("MAIL")
        session.handle("LIRE 1")
        self.assertIn("DESTINATAIRE", session.handle("REPONDRE oui")["display"])
        self.assertIsNone(session.draft)
        result = session.handle("DESTINATAIRE 2")
        self.assertIn("b@example.org", result["display"])
        self.assertEqual(session.draft["recipient"], "b@example.org")

    def test_uncertain_send_retires_draft_and_does_not_retry(self):
        session, client, _ = make_session()
        ticket = draft(session)
        session.acknowledge(ticket)
        client.send.side_effect = auth.GmailError("send_uncertain")
        result = session.handle("ENVOYER", ticket)
        self.assertIn("incertain", result["display"])
        self.assertIn("Message-ID", result["display"])
        self.assertIsNone(session.draft)
        session.handle("ENVOYER", ticket)
        client.send.assert_called_once()

    def test_reset_blocks_late_generation_and_busy_send(self):
        session, client, model = make_session()
        ticket = draft(session)
        entered, release = threading.Event(), threading.Event()

        def blocked(*args):
            entered.set()
            release.wait(2)
            return "Stale reply"

        model.complete.side_effect = blocked
        result = []
        worker = threading.Thread(
            target=lambda: result.append(session.handle("MODIFIER shorter"))
        )
        worker.start()
        self.assertTrue(entered.wait(1))
        self.assertIn("busy", session.handle("ENVOYER", ticket)["display"])
        session.reset()
        release.set()
        worker.join(2)
        self.assertIsNone(session.draft)
        self.assertIn("session_reset", result[0]["display"])
        client.send.assert_not_called()

    def test_context_limit_is_disclosed_and_only_selection_sent_to_model(self):
        session, _client, model = make_session("x" * (mail.CONTEXT_LIMIT + 300))
        session.handle("MAIL")
        result = session.handle("LIRE 1")
        self.assertIn("12000", result["display"])
        context = model.complete.call_args.args[1]
        self.assertEqual(len(context["email"]), mail.CONTEXT_LIMIT)
        self.assertNotIn("raw", context)
        self.assertNotIn("refresh_token", context)
        self.assertIn("x" * 12300, session.handle("TEXTE")["display"])

    def test_ai_saying_envoyer_never_sends(self):
        session, client, _ = make_session(output="ENVOYER")
        ticket = draft(session)
        session.acknowledge(ticket)
        client.send.assert_not_called()


class ApiTests(unittest.TestCase):
    def test_only_metadata_list_and_get_full_on_selection(self):
        client = api.GmailClient(tokens=Mock())
        client.request = Mock(
            side_effect=[{"messages": [{"id": "abc123"}]}, message(), message()]
        )
        self.assertEqual(
            client.list("is:unread", time.monotonic() + 10)[0]["id"], "abc123"
        )
        self.assertEqual(
            client.request.call_args.kwargs["params"]["format"], "metadata"
        )
        client.read("abc123", time.monotonic() + 10)
        self.assertEqual(client.request.call_args.kwargs["params"]["format"], "full")
        self.assertTrue(all(c.args[0] == "GET" for c in client.request.call_args_list))

    def test_identity_mismatch(self):
        client = api.GmailClient(tokens=Mock())
        client.request = Mock(return_value=message("other"))
        with self.assertRaisesRegex(auth.GmailError, "message_identity_mismatch"):
            client.read("abc123", time.monotonic() + 10)

    def test_uncertain_single_post_reconciles_or_requires_gmail_verification(self):
        for found in ({"messages": [{"id": "sent123"}]}, {}):
            client = api.GmailClient(tokens=Mock())
            responses = [auth.GmailError("unavailable"), found]
            if found:
                responses.append(
                    message("sent123", headers={"Message-ID": "<id@example.org>"})
                )
            client.request = Mock(side_effect=responses)
            if found:
                self.assertEqual(
                    client.send(
                        b"mime", "thread", "<id@example.org>", time.monotonic() + 10
                    ),
                    "sent",
                )
            else:
                with self.assertRaisesRegex(auth.GmailError, "send_uncertain"):
                    client.send(
                        b"mime", "thread", "<id@example.org>", time.monotonic() + 10
                    )
            self.assertEqual(
                [c.args[0] for c in client.request.call_args_list],
                ["POST", "GET", "GET"] if found else ["POST", "GET"],
            )
            self.assertIn(
                "rfc822msgid:<id@example.org>",
                client.request.call_args_list[1].kwargs["params"]["q"],
            )

    def test_auth_failure_is_clear_and_never_logs_response(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.status_code = 401
        tokens, http = Mock(), Mock()
        tokens.token.return_value = "fake-access"
        http.request.return_value = response
        with self.assertRaisesRegex(auth.GmailError, "reconnect_required"):
            api.GmailClient(tokens, http).request(
                "GET", "/profile", time.monotonic() + 10
            )
        response.iter_content.assert_not_called()
        self.assertIsNone(tokens.credentials)


class AuthTests(unittest.TestCase):
    def test_exact_scopes_no_access_token_persisted(self):
        self.assertEqual(auth.validate_info(info()), info())
        for change in (
            {"scopes": ["bad"]},
            {"scopes": [{}]},
            {"token": "access"},
            {"token_uri": "https://evil.invalid"},
        ):
            with self.assertRaises(auth.GmailError):
                auth.validate_info({**info(), **change})

    def test_ciphertext_only_atomic_disk_write_and_no_tokens_in_args(self):
        raw = json.dumps(info()).encode()
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "encrypted"
            with patch.object(
                auth.subprocess,
                "run",
                side_effect=[
                    SimpleNamespace(returncode=0, stdout=b"ENCRYPTED"),
                    SimpleNamespace(returncode=0, stdout=raw),
                ],
            ) as run:
                auth.encrypt_info(info(), destination)
            self.assertEqual(destination.read_bytes(), b"ENCRYPTED")
            self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
            self.assertEqual(len(list(Path(directory).iterdir())), 1)
            self.assertNotIn("fake-refresh", str([c.args for c in run.call_args_list]))
            self.assertIn("--with-key=host", run.call_args_list[0].args[0])
            destination.write_bytes(b"OLD")
            with (
                patch.object(
                    auth.subprocess,
                    "run",
                    return_value=SimpleNamespace(returncode=1, stdout=b""),
                ),
                self.assertRaises(auth.GmailError),
            ):
                auth.encrypt_info(info(), destination)
            self.assertEqual(destination.read_bytes(), b"OLD")

    def test_plaintext_fallback_impossible_and_systemd_preflight_fails_closed(self):
        source = auth.TokenSource(credential_dir="/no/credentials")
        with self.assertRaisesRegex(auth.GmailError, "reconnect_required"):
            source.delivered_info()
        with (
            patch.object(
                auth.subprocess,
                "run",
                return_value=SimpleNamespace(returncode=0, stdout=b"systemd 247"),
            ),
            self.assertRaisesRegex(
                auth.GmailError, "encrypted_credentials_unsupported"
            ),
        ):
            auth.systemd_preflight()

    def test_memory_renewal_rotation_and_revocation(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "gmail").write_text(json.dumps(info()))
            helper = Mock(side_effect=[info(), {"status": "saved"}])
            source = auth.TokenSource(directory, helper, Path(directory) / "lock")

            def refreshed(credentials, request):
                credentials.token = "new-access"
                credentials._refresh_token = "replacement-refresh"
                from datetime import datetime, timedelta

                credentials.expiry = datetime.now() + timedelta(hours=1)

            with patch.object(Credentials, "refresh", refreshed):
                self.assertEqual(source.token(time.monotonic() + 10), "new-access")
                self.assertEqual(source.token(time.monotonic() + 10), "new-access")
            self.assertEqual(helper.call_count, 2)
            self.assertEqual(
                helper.call_args.args[0],
                {
                    "operation": "rotate",
                    "expected": "fake-refresh",
                    "replacement": "replacement-refresh",
                },
            )
            self.assertEqual(
                json.loads(Path(directory, "gmail").read_text())["refresh_token"],
                "fake-refresh",
            )
            source.credentials = None
            helper.side_effect = None
            helper.return_value = info()
            with (
                patch.object(
                    Credentials,
                    "refresh",
                    side_effect=RefreshError("DO NOT EXPOSE TOKEN"),
                ),
                self.assertRaisesRegex(auth.GmailError, "^reconnect_required$"),
            ):
                source.token(time.monotonic() + 10)

    def test_desktop_oauth_pkce_state_and_exact_scopes_offline(self):
        client = {
            "installed": {
                "client_id": info()["client_id"],
                "client_secret": info()["client_secret"],
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": auth.TOKEN_URI,
            }
        }
        flow = InstalledAppFlow.from_client_config(
            client, auth.SCOPES, state="fixed-state", autogenerate_code_verifier=True
        )
        flow.redirect_uri = "http://127.0.0.1:8765/oauth2callback"
        url, state = flow.authorization_url(prompt="consent")
        from urllib.parse import parse_qs, urlsplit

        query = parse_qs(urlsplit(url).query)
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertEqual(state, "fixed-state")
        self.assertEqual(query["access_type"], ["offline"])
        self.assertEqual(set(query["scope"][0].split()), set(auth.SCOPES))
        self.assertTrue(flow.code_verifier)

    def test_callback_rejects_bad_state_and_does_not_log_code(self):
        spec = importlib.util.spec_from_file_location(
            "gmail_enroll", Path(__file__).parents[1] / "scripts/gmail_enroll.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with (
            patch.object(module, "PORT", 0),
            module.callback_server("expected") as server,
        ):
            for state, accepted in (("wrong", False), ("expected", True)):
                worker = threading.Thread(target=server.handle_request)
                worker.start()
                with patch("sys.stderr", new_callable=io.StringIO) as output:
                    response = requests.get(
                        f"http://127.0.0.1:{server.server_port}/oauth2callback?state={state}&code=PRIVATE-CODE",
                        timeout=2,
                    )
                worker.join(2)
                self.assertEqual(response.status_code, 200 if accepted else 400)
                self.assertNotIn("PRIVATE-CODE", output.getvalue())
                self.assertEqual(server.code, "PRIVATE-CODE" if accepted else None)


class AdditionalContracts(unittest.TestCase):
    def test_natural_read_cannot_invent_a_number(self):
        session, client, model = make_session()
        session.handle("MAIL")
        model.route.return_value = {"action": "read", "number": 1}
        result = session.handle("Read the mail from an unspecified sender")
        self.assertIn("Choisissez", result["display"])
        client.read.assert_not_called()
        session.handle("Read message number 1")
        self.assertEqual(client.read.call_count, 1)

    def test_natural_search_cannot_invent_an_address_or_send_action(self):
        model = mail.MailModel()
        for result in (
            '{"action":"list","query":"from:invented@example.org"}',
            '{"action":"send"}',
        ):
            with (
                patch.object(model, "complete", return_value=result),
                self.assertRaises(auth.GmailError),
            ):
                model.route("Show mail from Alice", time.monotonic() + 10)

    def test_search_paris_boundaries_and_no_modification_endpoints(self):
        client = api.GmailClient(tokens=Mock())
        client.request = Mock(return_value={})
        client.list(
            "after:2026/10/01 before:2026/10/13 is:unread", time.monotonic() + 10
        )
        from datetime import datetime
        from zoneinfo import ZoneInfo

        expected = int(
            datetime(2026, 10, 1, tzinfo=ZoneInfo("Europe/Paris")).timestamp()
        )
        self.assertIn(
            "after:" + str(expected), client.request.call_args.kwargs["params"]["q"]
        )
        self.assertEqual(client.request.call_args.args[0], "GET")

    def test_missing_threading_headers_block_reply(self):
        session, client, _ = make_session()
        client.read.return_value = api.message_record(
            message(headers={"From": "owner@example.org", "Subject": "Test"}), True
        )
        session.handle("MAIL")
        session.handle("LIRE 1")
        self.assertIn(
            "threading_headers_invalid", session.handle("REPONDRE oui")["display"]
        )
        self.assertIsNone(session.draft)
        client.send.assert_not_called()

    def test_nested_plain_alternative_and_malformed_header(self):
        nested = {
            "mimeType": "multipart/mixed",
            "parts": [
                body("chosen plain"),
                body("ignored", "application/pdf", filename="a.pdf"),
            ],
        }
        part = {
            "mimeType": "multipart/alternative",
            "parts": [body("<p>HTML</p>", "text/html"), nested],
        }
        self.assertEqual(api.mime_content(part)[0], "chosen plain")
        malformed = body(
            "x",
            headers=[
                {"name": "Content-Type", "value": "text/plain\r\nBcc: evil@example.org"}
            ],
        )
        with self.assertRaisesRegex(auth.GmailError, "malformed_message"):
            api.mime_content(malformed)

    def test_sent_result_needs_matching_message_id(self):
        client = api.GmailClient(tokens=Mock())
        client.request = Mock(
            side_effect=[
                auth.GmailError("unavailable"),
                {"messages": [{"id": "sent123"}]},
                message("sent123", headers={"Message-ID": "<different@example.org>"}),
            ]
        )
        with self.assertRaisesRegex(auth.GmailError, "send_uncertain"):
            client.send(b"mime", "thread", "<id@example.org>", time.monotonic() + 10)
        self.assertEqual(
            sum(c.args[0] == "POST" for c in client.request.call_args_list), 1
        )

    def test_mail_model_has_no_tools_personality_or_bulk_history(self):
        model = mail.MailModel(settings=lambda: ("fake-key", "existing-model"))
        response = Mock()
        response.json.return_value = {
            "choices": [{"finish_reason": "stop", "message": {"content": "Summary"}}]
        }
        with patch.object(mail.requests, "post", return_value=response) as post:
            self.assertEqual(
                model.complete(
                    "Summarize",
                    {"email": "untrusted selected content"},
                    time.monotonic() + 10,
                ),
                "Summary",
            )
        payload = post.call_args.kwargs["json"]
        self.assertNotIn("tools", payload)
        self.assertNotIn("tool_choice", payload)
        self.assertNotIn("Minitel 722079V", str(payload))
        self.assertIn("untrusted quoted data", payload["messages"][0]["content"])
        self.assertEqual(payload["model"], "existing-model")

    def test_root_rotation_compare_and_swap_and_no_arbitrary_fields(self):
        import sys

        spec = importlib.util.spec_from_file_location(
            "gmail_helper_test",
            Path(__file__).parents[1] / "scripts/gmail_credential_helper.py",
        )
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"gmail_auth": auth}):
            spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            lock = Path(directory, "root-lock")
            with (
                patch.object(module, "Path", return_value=lock),
                patch.object(module, "decrypt_info", side_effect=lambda: info()),
                patch.object(module, "encrypt_info") as encrypt,
            ):
                self.assertEqual(module.process({"operation": "read"}), info())
                for request in (
                    {"operation": "rotate", "expected": "wrong", "replacement": "new"},
                    {
                        "operation": "rotate",
                        "expected": "fake-refresh",
                        "replacement": "new",
                        "path": "/tmp/plaintext",
                    },
                ):
                    with self.assertRaises(auth.GmailError):
                        module.process(request)
                encrypt.assert_not_called()
                self.assertEqual(
                    module.process(
                        {
                            "operation": "rotate",
                            "expected": "fake-refresh",
                            "replacement": "new",
                        }
                    ),
                    {"status": "saved"},
                )
                self.assertEqual(encrypt.call_args.args[0]["refresh_token"], "new")
                self.assertEqual(encrypt.call_args.args[0]["scopes"], list(auth.SCOPES))


if __name__ == "__main__":
    unittest.main()
