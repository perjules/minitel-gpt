"""Grounded new composition. All REST/model calls fake; no live sending."""

import base64
import json
import threading
import time
import unittest
from email import policy
from email.parser import BytesParser
from unittest.mock import Mock, patch

from test_gmail import make_session, message

from services import gmail_client as api
from services import gmail_correspondents as extraction
from services import gmail_mail as mail
from services.gmail_auth import GmailError


def metadata(identifier="recent1", **headers):
    result = message(
        identifier,
        text="BODY MUST NOT BE USED",
        headers=headers
        or {
            "From": "Alice <alice@example.org>",
            "To": "Me <me@example.org>",
            "Subject": "Old unrelated topic",
            "Date": "Tue, 13 Oct 2026 12:00:00 +0200",
        },
    )
    result["payload"] = {"headers": result["payload"]["headers"]}
    result["snippet"] = "SNIPPET MUST NOT BE USED"
    return result


def compose_session():
    session, client, model = make_session(text="SELECTED BODY MUST NOT BE USED")
    client.correspondent_metadata.return_value = {
        "records": [metadata()],
        "attempted": 1,
        "failed": 0,
        "partial": False,
    }
    model.resolve.side_effect = lambda description, candidates, deadline: {
        "status": "unique",
        "ids": [candidates[0]["id"]],
    }
    model.composition.return_value = (
        "Rendez-vous mardi",
        "Bonjour, rendez-vous mardi.",
    )
    return session, client, model


class ExtractionTests(unittest.TestCase):
    def test_names_direction_roles_distinct_reply_to_and_conservative_dedup(self):
        incoming = metadata(
            From="=?UTF-8?B?QWxpY2Ugw4lsb2RpZQ==?= <alice@example.org>",
            **{
                "Reply-To": "Assistant <assistant@example.org>",
                "To": "me@example.org",
                "Cc": "Alias <self-alias@example.org>",
                "Subject": "Sujet récent",
            },
        )
        sent = metadata(
            "sent",
            From="me@example.org",
            To='"A. Élodie" <alice@EXAMPLE.ORG>, Alice <other@example.org>',
            Subject="Autre sujet",
        )
        sent["labelIds"] = ["SENT"]
        sent["internalDate"] = "1791806400001"
        archived = metadata(
            "archived", From="no-reply@example.org", To="me@example.org", Subject="Avis"
        )
        archived["labelIds"] = []
        candidates, skipped = extraction.catalogue(
            {"records": [incoming, sent, archived]},
            ["me@example.org", "self-alias@example.org"],
        )
        by_address = {c["address"].lower(): c for c in candidates}
        self.assertEqual(
            set(by_address),
            {
                "alice@example.org",
                "other@example.org",
                "assistant@example.org",
                "no-reply@example.org",
            },
        )
        alice = by_address["alice@example.org"]
        self.assertEqual(alice["names"], ["A. Élodie", "Alice Élodie"])
        self.assertEqual(alice["subjects"], ["Autre sujet", "Sujet récent"])
        self.assertIn("envoyé: To", alice["roles"])
        self.assertIn("reçu: From", alice["roles"])
        self.assertIn(
            "reçu / archivé: From", by_address["no-reply@example.org"]["roles"]
        )
        self.assertTrue(by_address["no-reply@example.org"]["automated"])
        self.assertEqual(skipped, 0)
        self.assertNotIn("BODY", json.dumps(candidates))
        self.assertNotIn("SNIPPET", json.dumps(candidates))

    def test_malformed_duplicate_headers_and_dates_do_not_invent_addresses(self):
        bad = metadata(
            From="Alice <not-an-address>",
            To="valid@example.org",
            Subject="short",
            Date="nonsense",
        )
        bad["internalDate"] = "nonsense"
        bad["payload"]["headers"] += [
            {"name": "Cc", "value": "one@example.org"},
            {"name": "Cc", "value": "two@example.org"},
        ]
        values, skipped = extraction.catalogue({"records": [bad]}, ["me@example.org"])
        self.assertEqual([c["address"] for c in values], ["valid@example.org"])
        self.assertEqual(values[0]["last_seen"], "Date inconnue")
        self.assertEqual(skipped, 2)

    def test_ram_bound_is_honest_and_exact_address_narrowing_is_grounded(self):
        data = {"records": [metadata(To="a@example.org,b@example.org,c@example.org")]}
        with patch.object(extraction, "CATALOGUE_LIMIT", 400):
            with self.assertRaisesRegex(GmailError, "catalogue_too_large"):
                extraction.catalogue(data, ["me@example.org"])
            values, _ = extraction.catalogue(
                data, ["me@example.org"], ["b@example.org"]
            )
            self.assertEqual([c["address"] for c in values], ["b@example.org"])
            self.assertEqual(
                extraction.catalogue(data, [], ["invented@example.org"])[0], []
            )


class CorrespondenceApiTests(unittest.TestCase):
    def test_fifty_metadata_only_requests_use_five_distinct_sessions(self):
        client = api.GmailClient(tokens=Mock())
        identifiers = ["m" + str(i) for i in range(50)]
        lock, barrier = threading.Lock(), threading.Barrier(5)
        calls, workers = [], {}

        def request(source, method, path, deadline, params=None, payload=None):
            with lock:
                calls.append((method, path, params))
            if path == "/messages":
                return {"messages": [{"id": i} for i in identifiers]}
            worker = threading.get_ident()
            if worker not in workers:
                workers[worker] = source.http
                barrier.wait(2)
            record = metadata(path.rsplit("/", 1)[-1])
            record.pop("snippet")
            return record

        with patch.object(
            api.GmailClient, "request", autospec=True, side_effect=request
        ):
            result = client.correspondent_metadata(time.monotonic() + 10)
        self.assertEqual(len(result["records"]), 50)
        self.assertEqual(result["attempted"], 50)
        self.assertFalse(result["partial"])
        self.assertEqual(len(workers), 5)
        self.assertEqual(len({id(s) for s in workers.values()}), 5)
        self.assertEqual(len(calls), 51)
        for method, path, params in calls:
            self.assertEqual(method, "GET")
            if path == "/messages":
                self.assertEqual(params["maxResults"], 50)
                self.assertIn("-in:drafts", params["q"])
                self.assertNotIn("labelIds", params)
            else:
                self.assertEqual(params["format"], "metadata")
                self.assertEqual(params["metadataHeaders"], api.CORRESPONDENT_HEADERS)
                self.assertNotIn("snippet", params["fields"])
                self.assertNotIn("body", params["fields"])

    def test_partial_failure_exclusions_and_scope_failure(self):
        client = api.GmailClient(tokens=Mock())

        def request(source, method, path, deadline, params=None):
            if path == "/messages":
                return {"messages": [{"id": v} for v in ["ok", "bad", "draft"]]}
            if path.endswith("bad"):
                raise GmailError("unavailable")
            record = metadata(path.rsplit("/", 1)[-1])
            if path.endswith("draft"):
                record["labelIds"] = ["DRAFT"]
            return record

        with patch.object(
            api.GmailClient, "request", autospec=True, side_effect=request
        ):
            result = client.correspondent_metadata(time.monotonic() + 10)
        self.assertEqual([r["id"] for r in result["records"]], ["ok"])
        self.assertTrue(result["partial"])
        self.assertEqual(result["failed"], 1)
        with (
            patch.object(
                api.GmailClient, "request", side_effect=GmailError("reconnect_required")
            ),
            self.assertRaisesRegex(GmailError, "reconnect_required"),
        ):
            client.correspondent_metadata(time.monotonic() + 10)

    def test_new_send_omits_thread_id_and_uncertainty_never_retries(self):
        client = api.GmailClient(tokens=Mock())
        client.request = Mock(return_value={"id": "sent123"})
        self.assertEqual(
            client.send(
                b"fresh MIME", None, "<new@example.org>", time.monotonic() + 10
            ),
            "sent",
        )
        payload = client.request.call_args.kwargs["payload"]
        self.assertEqual(set(payload), {"raw"})
        self.assertEqual(base64.urlsafe_b64decode(payload["raw"]), b"fresh MIME")
        client.request = Mock(side_effect=[GmailError("unavailable"), {}])
        with self.assertRaisesRegex(GmailError, "send_uncertain"):
            client.send(b"fresh MIME", None, "<new@example.org>", time.monotonic() + 10)
        self.assertEqual(
            [c.args[0] for c in client.request.call_args_list], ["POST", "GET"]
        )

    def test_short_api_pages_filled_without_exceeding_fifty(self):
        client = api.GmailClient(tokens=Mock())
        seen = []

        def request(source, method, path, deadline, params=None):
            if path == "/messages":
                seen.append(params)
                if "pageToken" not in params:
                    return {"messages": [{"id": "first"}], "nextPageToken": "next"}
                return {"messages": [{"id": "second"}]}
            return metadata(path.rsplit("/", 1)[-1])

        with patch.object(
            api.GmailClient, "request", autospec=True, side_effect=request
        ):
            result = client.correspondent_metadata(time.monotonic() + 10)
        self.assertEqual(result["attempted"], 2)
        self.assertEqual([p["maxResults"] for p in seen], [50, 49])


class ModelContractTests(unittest.TestCase):
    def test_resolver_schema_unknown_duplicate_ids_and_confidence_are_rejected(self):
        model = mail.MailModel()
        candidates = [{"id": "real", "address": "alice@example.org"}]
        invalid = [
            {"status": "unique", "ids": ["invented"]},
            {"status": "unique", "ids": ["real", "real"]},
            {"status": "unique", "ids": ["real"], "confidence": 1},
            {"status": "unique", "ids": [], "address": "invented@example.org"},
            {"status": "ambiguous", "ids": ["real"]},
        ]
        for value in invalid:
            with (
                self.subTest(value=value),
                patch.object(model, "complete", return_value=json.dumps(value)),
                self.assertRaisesRegex(GmailError, "recipient_resolution_invalid"),
            ):
                model.resolve("Alice", candidates, time.monotonic() + 10)
        with patch.object(
            model, "complete", return_value='{"status":"unique","ids":["real"]}'
        ) as complete:
            self.assertEqual(
                model.resolve("Alcie", candidates, time.monotonic() + 10)["ids"],
                ["real"],
            )
            self.assertTrue(complete.call_args.kwargs["structured"])
            self.assertIn(
                "confidence is not identity evidence", complete.call_args.args[0]
            )

    def test_new_subject_body_signature_and_header_injection_rejection(self):
        model = mail.MailModel()
        with patch.object(
            model,
            "complete",
            return_value=json.dumps({"subject": "Bonjour", "body": "Merci"}),
        ):
            subject, body = model.composition(
                "remercier", {"address": "a@example.org"}, time.monotonic() + 10
            )
            self.assertEqual(subject, "Bonjour")
            self.assertEqual(body.count(mail.SIGNATURE), 1)
        for value in [
            {"subject": "ok\r\nBcc: evil@example.org", "body": "ok"},
            {"subject": "ok", "body": "ok", "to": "evil@example.org"},
        ]:
            with (
                patch.object(model, "complete", return_value=json.dumps(value)),
                self.assertRaisesRegex(GmailError, "invalid_composition"),
            ):
                model.composition("x", {}, time.monotonic() + 10)

    def test_router_keeps_identity_and_writing_instruction_separate(self):
        model = mail.MailModel()
        route = {
            "action": "compose",
            "recipient": "Alice",
            "instruction": "confirmer mardi",
        }
        with patch.object(
            model, "complete", return_value=json.dumps(route)
        ) as complete:
            self.assertEqual(
                model.route(
                    "écris un mail à Alice pour confirmer mardi", time.monotonic() + 10
                ),
                route | {"original_request": "écris un mail à Alice pour confirmer mardi"},
            )
            self.assertEqual(
                complete.call_args.args[1]["request"],
                "écris un mail à Alice pour confirmer mardi",
            )
            self.assertNotIn("candidates", complete.call_args.args[1])


class CompositionSessionTests(unittest.TestCase):
    def literal_session(self):
        _, client, model = compose_session()
        model.composition.return_value = ("Mardi", "Bonjour.\n\nRendez-vous mardi.")
        session = mail.MailSession(client, model, username="Camille")
        session.handle("ECRIRE Alice : mardi")
        model.reset_mock()
        return session, client, model

    def test_literal_aliases_insert_and_replace_exact_paragraphs_without_models(self):
        for command in ("AJOUTER 1: Écoute : ni oui, ni non.", "A1: Écoute : ni oui, ni non.", "a 1 : Écoute : ni oui, ni non."):
            with self.subTest(command=command):
                session, client, model = self.literal_session()
                old = session.draft["ticket"]
                session.acknowledge(old)
                result = session.handle(command)
                self.assertEqual(session.draft["body"], "Bonjour.\n\nÉcoute : ni oui, ni non.\n\nRendez-vous mardi.\n\nCamille\n\n" + mail.SIGNATURE)
                self.assertNotEqual(old, result["review_ticket"])
                self.assertFalse(session.review_valid(old))
                self.assertFalse(session.review_valid(result["review_ticket"]))
                self.assertIn("2. Écoute", result["display"])
                self.assertNotIn("2. Écoute", session.draft["body"])
                self.assertEqual(model.method_calls, [])
                client.send.assert_not_called()
        for command in ("REMPLACER 2: Texte personnel.", "R2: Texte personnel."):
            session, _, model = self.literal_session()
            session.handle(command)
            self.assertEqual(session.draft["body"], "Bonjour.\n\nTexte personnel.\n\nCamille\n\n" + mail.SIGNATURE)
            self.assertEqual(model.method_calls, [])

    def test_literal_append_precedes_default_or_literally_replaced_signoff(self):
        for command in ("AJOUTER: À bientôt.", "ajouter: À bientôt."):
            session, _, _ = self.literal_session()
            session.handle("R3: Alex")
            session.handle(command)
            self.assertEqual(session.draft["body"], "Bonjour.\n\nRendez-vous mardi.\n\nÀ bientôt.\n\nAlex\n\n" + mail.SIGNATURE)

    def test_literal_full_body_is_verbatim_and_footer_remains_mandatory(self):
        for command in ("REMPLACER TOUT: ", "REMPLACER: ", "R: "):
            session, client, model = self.literal_session()
            text = "Bonjour : test.\n\nRETIENS ceci : ENVOYER / MAIL / IMPRIMER."
            session.handle(command + text)
            self.assertEqual(session.draft["body"], text + "\n\n" + mail.SIGNATURE)
            self.assertNotIn("Camille", session.draft["body"])
            self.assertEqual(model.method_calls, [])
            client.send.assert_not_called()

    def test_invalid_literal_target_or_empty_text_leaves_body_and_approval_untouched(self):
        for command in ("A0: x", "R99: x", "AJOUTER TOUT: x", "R1:", "AJOUTER:", "R: "):
            with self.subTest(command=command):
                session, _, model = self.literal_session()
                ticket = session.draft["ticket"]
                session.acknowledge(ticket)
                previous = dict(session.draft)
                self.assertIn("inchangé", session.handle(command)["display"])
                self.assertEqual(session.draft, previous)
                self.assertTrue(session.review_valid(ticket))
                self.assertEqual(model.method_calls, [])

    def test_literal_edit_preserves_reply_headers_and_does_not_refetch_mail(self):
        from test_gmail import draft
        session, client, model = make_session()
        draft(session)
        previous = dict(session.draft)
        model.reset_mock()
        reads = client.read.call_count
        ticket = session.handle("R1: Ma réponse exacte : oui.")["review_ticket"]
        mime = BytesParser(policy=policy.default).parsebytes(session.draft["raw"])
        self.assertEqual(mime["In-Reply-To"], "<original@example.org>")
        self.assertEqual(session.draft["thread"], previous["thread"])
        self.assertEqual(session.draft["recipient"], previous["recipient"])
        self.assertEqual(session.draft["subject"], previous["subject"])
        self.assertEqual(client.read.call_count, reads)
        self.assertEqual(model.method_calls, [])
        session.handle("ENVOYER", ticket)
        client.send.assert_not_called()
        session.acknowledge(ticket)
        session.handle("ENVOYER", ticket)
        client.send.assert_called_once()

    def test_initial_signoff_defaults_to_current_username_or_minitelgpt(self):
        for username, expected in (("Camille", "Camille"), ("", "MinitelGPT")):
            with self.subTest(username=username):
                _, client, model = compose_session()
                session = mail.MailSession(client, model, username=username)
                result = session.handle("ECRIRE Alice : mardi")
                self.assertIn("review_ticket", result)
                self.assertTrue(session.draft["body"].endswith("\n\n" + expected + "\n\n" + mail.SIGNATURE))
                mime = BytesParser(policy=policy.default).parsebytes(session.draft["raw"])
                self.assertIn(expected, mime.get_content())
                self.assertEqual(mime["From"], "me@example.org")
                client.send.assert_not_called()

    def test_personal_name_can_be_replaced_or_removed_but_footer_is_always_restored(self):
        _, client, model = compose_session()
        session = mail.MailSession(client, model, username="Camille")
        session.handle("ECRIRE Alice : mardi")
        self.assertIn("\n\nCamille\n\n", session.draft["body"])
        for instruction, body in (("Signe avec Alex", "Bonjour.\n\nAlex"),
                                  ("Retire mon nom et le pied de page Minitel", "Bonjour.")):
            with self.subTest(instruction=instruction):
                model.composition.return_value = ("Mardi", body)
                session.handle("MODIFIER")
                session.handle(instruction)
                self.assertEqual(session.draft["body"], body + "\n\n" + mail.SIGNATURE)
                self.assertNotIn("Camille", session.draft["body"])
                self.assertEqual(session.draft["body"].count(mail.SIGNATURE), 1)
                self.assertFalse(session.draft["reviewed"])
        client.send.assert_not_called()

    def test_modify_then_personal_signoff_edits_existing_new_message_without_router(self):
        session, client, _ = compose_session()
        old_ticket = session.handle("ECRIRE Alice : confirmer mardi")["review_ticket"]
        previous = dict(session.draft)
        session.acknowledge(old_ticket)
        session.model = mail.MailModel()
        revised_body = previous["body"].removesuffix("\n\n" + mail.SIGNATURE) + "\n\nCamille"
        with (
            patch.object(session.model, "route", side_effect=AssertionError("Edit was reclassified")),
            patch.object(session.model, "complete", return_value=json.dumps({
                "subject": previous["subject"], "body": revised_body,
            })) as complete,
        ):
            session.handle("MODIFIER")
            self.assertTrue(session.editing)
            result = session.handle("Signe avec Camille")
        complete.assert_called_once()
        prompt, data = complete.call_args.args[:2]
        self.assertEqual(data["task"], "revise")
        self.assertEqual(data["instruction"], "Signe avec Camille")
        self.assertEqual(data["existing_message"]["subject"], previous["subject"])
        self.assertEqual(data["existing_message"]["body"], previous["body"].removesuffix("\n\n" + mail.SIGNATURE))
        self.assertIn("sender's closing name", prompt)
        self.assertNotIn("No signature", prompt)
        self.assertEqual(session.draft["recipient"], previous["recipient"])
        self.assertEqual(session.draft["subject"], previous["subject"])
        self.assertEqual(session.draft["revision"], 2)
        self.assertIn("\n\nCamille\n\n" + mail.SIGNATURE, session.draft["body"])
        self.assertEqual(session.draft["body"].count(mail.SIGNATURE), 1)
        self.assertFalse(session.editing)
        self.assertFalse(session.review_valid(old_ticket))
        ticket = result["review_ticket"]
        session.handle("ENVOYER", ticket)
        client.send.assert_not_called()
        session.acknowledge(ticket)
        session.handle("ENVOYER", ticket)
        client.send.assert_called_once()

    def test_failed_explicit_edit_keeps_edit_mode_and_old_review_invalid(self):
        session, client, model = compose_session()
        ticket = session.handle("ECRIRE Alice : mardi")["review_ticket"]
        session.acknowledge(ticket)
        session.handle("MODIFIER")
        model.composition.side_effect = GmailError("generation_unavailable")
        session.handle("Signe avec Camille")
        self.assertTrue(session.editing)
        self.assertFalse(session.review_valid(ticket))
        model.route.assert_not_called()
        client.send.assert_not_called()
        session.handle("ANNULER")
        self.assertFalse(session.editing)
        self.assertIsNone(session.draft)

    def test_unknown_contact_then_literal_address_keeps_instructions_and_send_review(self):
        session, client, model = compose_session()
        model.resolve.side_effect = None
        model.resolve.return_value = {"status": "no_match", "ids": []}
        result = session.handle("ECRIRE Bob : confirmer mardi")
        self.assertIn("introuvable", result["display"])
        result = session.handle("mail+new@example.net")
        ticket = result["review_ticket"]
        self.assertEqual(session.draft["recipient"], "mail+new@example.net")
        self.assertEqual(model.composition.call_args.args[0], "confirmer mardi")
        self.assertEqual(model.composition.call_args.args[1]["address"], "mail+new@example.net")
        model.route.assert_not_called()
        model.resolve.assert_called_once()
        client.correspondent_metadata.assert_called_once()
        mime = BytesParser(policy=policy.default).parsebytes(session.draft["raw"])
        self.assertEqual(mime["To"], "mail+new@example.net")
        self.assertIsNone(mime["In-Reply-To"])
        session.handle("ENVOYER", ticket)
        client.send.assert_not_called()
        session.acknowledge(ticket)
        session.handle("ENVOYER", ticket)
        client.send.assert_called_once()

    def test_literal_shortcut_needs_no_correspondent_lookup_or_resolution(self):
        session, client, model = compose_session()
        result = session.handle("ECRIRE never-seen@example.net : confirmer mardi")
        self.assertIn("review_ticket", result)
        self.assertEqual(session.draft["recipient"], "never-seen@example.net")
        client.correspondent_metadata.assert_not_called()
        model.resolve.assert_not_called()
        model.route.assert_not_called()
        client.send.assert_not_called()

    def test_sentence_with_address_after_failed_lookup_uses_router(self):
        for request, action, instruction in (
            ("send a mail to bob@web.de to confirm Friday", "compose", "confirm Friday"),
            ("Envoie un mail à bob@hotmail.com", "compose", ""),
            ("Peux-tu envoyer un mail à bob@hotmail.com", "compose", ""),
            ("Please send a mail to bob@web.de", "compose", ""),
            ("send it to bob@stellantis.com", "retarget", ""),
        ):
            with self.subTest(request=request):
                session, client, model = compose_session()
                model.resolve.side_effect = None
                model.resolve.return_value = {"status": "no_match", "ids": []}
                session.handle("ECRIRE Bob : confirmer mardi")
                address = request.split("@", 1)[1].split()[0]
                model.route.return_value = {
                    "action": action,
                    "recipient": "bob@" + address,
                    "instruction": instruction,
                }
                result = session.handle(request)
                self.assertIn("review_ticket", result)
                self.assertEqual(session.draft["recipient"], "bob@" + address)
                self.assertEqual(
                    model.composition.call_args.args[0], instruction or "confirmer mardi"
                )
                model.route.assert_called_once()
                self.assertTrue(model.route.call_args.kwargs["context"]["pending_composition"])
                client.correspondent_metadata.assert_called_once()
                client.send.assert_not_called()

    def test_literal_address_replaces_ambiguous_choices(self):
        session, client, model = compose_session()
        client.correspondent_metadata.return_value["records"].append(
            metadata("other", From="Alice <other@example.org>"))
        model.resolve.side_effect = lambda d, c, t: {"status": "ambiguous", "ids": [v["id"] for v in c]}
        session.handle("ECRIRE Alice : confirmer mardi")
        self.assertEqual(len(session.recipient_choices), 2)
        self.assertIn("review_ticket", session.handle("new@example.net"))
        self.assertEqual(session.draft["recipient"], "new@example.net")
        self.assertEqual(session.recipient_choices, [])
        model.resolve.assert_called_once()

    def test_invalid_literal_addresses_keep_pending_instructions_without_drafting(self):
        for address in ("bad@@example.org", "a@example.org, b@example.org",
                        "good@example.org\r\n Bcc: other@example.org"):
            with self.subTest(address=address):
                session, client, model = compose_session()
                model.resolve.side_effect = None
                model.resolve.return_value = {"status": "no_match", "ids": []}
                session.handle("ECRIRE Bob : confirmer mardi")
                self.assertIn("mal formée", session.handle(address)["display"])
                self.assertEqual(session.composition["instruction"], "confirmer mardi")
                self.assertIsNone(session.draft)
                model.composition.assert_not_called()
                client.send.assert_not_called()

    def test_direct_address_without_content_asks_only_for_content(self):
        session, client, model = compose_session()
        model.route.return_value = {"action": "compose", "recipient": "new@example.net", "instruction": ""}
        self.assertIn("voulez-vous écrire", session.handle("Écris un mail à new@example.net")["display"])
        self.assertIn("review_ticket", session.handle("Contacter support@example.org mardi"))
        self.assertEqual(session.draft["recipient"], "new@example.net")
        self.assertEqual(model.composition.call_args.args[0], "Contacter support@example.org mardi")
        client.correspondent_metadata.assert_not_called()
        model.resolve.assert_not_called()

    def test_many_plausible_matches_ask_for_detail_instead_of_a_long_menu(self):
        session, client, model = compose_session()
        client.correspondent_metadata.return_value["records"] = [
            metadata("m" + str(i), From=f"Alice <a{i}@example.org>") for i in range(7)
        ]
        model.resolve.side_effect = lambda d, c, t: {
            "status": "ambiguous",
            "ids": [v["id"] for v in c],
        }
        result = session.handle("ECRIRE Alice : mardi")
        self.assertIn("7 correspondants", result["display"])
        self.assertEqual(session.recipient_choices, [])
        self.assertEqual(session.composition["instruction"], "mardi")
        model.composition.assert_not_called()

    def test_natural_infinitive_and_draft_to_are_new_not_reply_shortcuts(self):
        for request in [
            "Écrire un mail à Alice pour confirmer mardi",
            "Draft an email to Alice confirming Tuesday",
        ]:
            session, _client, model = compose_session()
            model.route.return_value = {
                "action": "compose",
                "recipient": "Alice",
                "instruction": "confirmer mardi",
            }
            self.assertIn("review_ticket", session.handle(request))
            self.assertEqual(session.draft["kind"], "new")
            self.assertIsNone(session.selected)

    def test_missing_content_is_clarified_before_fetch_and_keeps_recipient(self):
        session, client, model = compose_session()
        model.route.return_value = {
            "action": "compose",
            "recipient": "Alice",
            "instruction": "",
        }
        result = session.handle("Écris un mail à Alice")
        self.assertIn("voulez-vous écrire", result["display"])
        client.correspondent_metadata.assert_not_called()
        self.assertIn("review_ticket", session.handle("confirmer mardi"))
        self.assertEqual(model.resolve.call_args.args[0], "Alice")
        self.assertEqual(model.composition.call_args.args[0], "confirmer mardi")

    def test_shortcut_no_selection_fresh_mime_full_review_signature_one_send(self):
        session, client, model = compose_session()
        result = session.handle("ECRIRE Alice : confirmer mardi")
        self.assertIn("review_ticket", result)
        model.route.assert_not_called()
        client.list.assert_not_called()
        client.read.assert_not_called()
        draft = session.draft
        parsed = BytesParser(policy=policy.default).parsebytes(draft["raw"])
        self.assertEqual(parsed["To"], "alice@example.org")
        self.assertEqual(parsed["From"], "me@example.org")
        self.assertEqual(parsed["Subject"], "Rendez-vous mardi")
        for header in ["In-Reply-To", "References", "Cc", "Bcc"]:
            self.assertIsNone(parsed[header])
        self.assertIsNone(draft["thread"])
        self.assertIsNone(draft["parent"])
        self.assertEqual(draft["kind"], "new")
        self.assertEqual(parsed.get_content().count(mail.SIGNATURE), 1)
        self.assertIn("Date", parsed)
        self.assertIn("Message-ID", parsed)
        ticket = result["review_ticket"]
        session.handle("ENVOYER", ticket)
        client.send.assert_not_called()
        session.acknowledge(ticket)
        calls = model.method_calls[:]
        session.handle("ENVOYER", ticket)
        self.assertEqual(calls, model.method_calls)
        client.send.assert_called_once_with(
            draft["raw"], None, draft["message_id"], unittest.mock.ANY
        )
        session.handle("ENVOYER", ticket)
        client.send.assert_called_once()

    def test_natural_compose_beats_selected_message_and_pending_reply(self):
        session, _client, model = compose_session()
        session.handle("MAIL")
        session.handle("LIRE 1")
        old = session.handle("REPONDRE oui")["review_ticket"]
        session.acknowledge(old)
        model.route.return_value = {
            "action": "compose",
            "recipient": "Alcie",
            "instruction": "confirmer mardi",
        }
        result = session.handle("Écris un mail à Alcie pour confirmer mardi")
        self.assertIn("review_ticket", result)
        self.assertFalse(session.review_valid(old))
        self.assertEqual(session.draft["recipient"], "alice@example.org")
        self.assertEqual(session.draft["kind"], "new")
        self.assertNotIn("SELECTED BODY", str(model.composition.call_args))
        self.assertNotIn("Old unrelated topic", str(model.composition.call_args))
        self.assertIsNone(session.draft["thread"])

    def test_ambiguous_number_and_conversational_choice_keep_instruction(self):
        for command in ["DESTINATAIRE 2", "celle de l'autre adresse"]:
            session, client, model = compose_session()
            client.correspondent_metadata.return_value["records"].append(
                metadata("recent2", From="Alice <second@example.org>")
            )
            model.resolve.side_effect = lambda d, c, t: {
                "status": "ambiguous",
                "ids": [v["id"] for v in c],
            }
            result = session.handle("ECRIRE Alice : confirmer mardi")
            self.assertIn("DESTINATAIRE", result["display"])
            self.assertIsNone(session.draft)
            model.composition.assert_not_called()
            if not command.startswith("DESTINATAIRE"):
                model.resolve.side_effect = lambda d, c, t: {
                    "status": "unique",
                    "ids": [c[1]["id"]],
                }
            result = session.handle(command)
            self.assertIn("review_ticket", result)
            self.assertEqual(session.draft["recipient"], "second@example.org")
            self.assertEqual(model.composition.call_args.args[0], "confirmer mardi")
            client.correspondent_metadata.assert_called_once()

    def test_no_match_unknown_id_and_partial_failures_are_visible(self):
        session, client, model = compose_session()
        client.correspondent_metadata.return_value.update(
            attempted=50, failed=49, partial=True
        )
        model.resolve.side_effect = None
        model.resolve.return_value = {"status": "no_match", "ids": []}
        result = session.handle("ECRIRE Bob : confirmer mardi")
        self.assertIn("partiels", result["display"])
        self.assertIn("introuvable", result["display"])
        self.assertIsNone(session.draft)
        model.resolve.return_value = {"status": "unique", "ids": ["unknown"]}
        result = session.handle("ECRIRE Bob : confirmer mardi")
        self.assertIn("sélection", result["display"])
        model.composition.assert_not_called()
        client.send.assert_not_called()

    def test_catalogue_ttl_and_reset_do_not_disturb_ten_message_list(self):
        session, client, _model = compose_session()
        session.handle("MAIL")
        original = session.items[:]
        session.handle("ECRIRE Alice : mardi")
        session.handle("ECRIRE Alice : mercredi")
        client.correspondent_metadata.assert_called_once()
        self.assertEqual(session.items, original)
        self.assertEqual(session.loaded, {})
        session.catalogue["expires"] = 0
        session.handle("ECRIRE Alice : jeudi")
        self.assertEqual(client.correspondent_metadata.call_count, 2)
        session.handle("SOMMAIRE")
        self.assertIsNone(session.catalogue)
        self.assertIsNone(session.composition)
        session.handle("ECRIRE Alice : vendredi")
        self.assertEqual(client.correspondent_metadata.call_count, 3)

    def test_model_context_overflow_accepts_a_literal_address_without_more_lookup(self):
        session, client, model = compose_session()
        with patch.object(extraction, "MODEL_CATALOGUE_LIMIT", 50):
            result = session.handle("ECRIRE Alice : mardi")
        self.assertIn("volumineux", result["display"])
        model.resolve.assert_not_called()
        session.handle("alice@example.org")
        self.assertEqual(session.draft["recipient"], "alice@example.org")
        client.correspondent_metadata.assert_called_once()

    def test_revision_subject_and_recipient_change_invalidate_old_review(self):
        session, client, model = compose_session()
        ticket = session.handle("ECRIRE Alice : mardi")["review_ticket"]
        session.acknowledge(ticket)
        model.composition.return_value = ("Nouvel objet", "Plus court.")
        newer = session.handle("MODIFIER raccourcir et changer l'objet")[
            "review_ticket"
        ]
        self.assertFalse(session.review_valid(ticket))
        self.assertEqual(session.draft["subject"], "Nouvel objet")
        self.assertEqual(session.draft["revision"], 2)
        self.assertIsNone(session.draft["thread"])
        session.acknowledge(newer)
        model.route.return_value = {
            "action": "retarget",
            "recipient": "Alice",
            "instruction": "",
        }
        result = session.handle("Change the recipient to Alice")
        self.assertIn("review_ticket", result)
        self.assertFalse(session.review_valid(newer))
        self.assertEqual(session.draft["revision"], 3)
        client.send.assert_not_called()

    def test_failed_new_intent_and_failed_generation_cannot_send_old_review(self):
        session, client, model = compose_session()
        old = session.handle("ECRIRE Alice : mardi")["review_ticket"]
        session.acknowledge(old)
        model.route.side_effect = GmailError("generation_unavailable")
        session.handle("Write a new email to Bob")
        self.assertFalse(session.review_valid(old))
        session.handle("ENVOYER", old)
        client.send.assert_not_called()
        model.composition.side_effect = GmailError("generation_unavailable")
        session.handle("ECRIRE Alice : autre mail")
        self.assertIsNone(session.draft)

    def test_reset_during_recipient_resolution_discards_late_result(self):
        session, client, model = compose_session()
        entered, release = threading.Event(), threading.Event()

        def blocked(d, c, t):
            entered.set()
            release.wait(2)
            return {"status": "unique", "ids": [c[0]["id"]]}

        model.resolve.side_effect = blocked
        results = []
        worker = threading.Thread(
            target=lambda: results.append(session.handle("ECRIRE Alice : mardi"))
        )
        worker.start()
        self.assertTrue(entered.wait(1))
        session.reset()
        release.set()
        worker.join(2)
        self.assertIn("session_reset", results[0]["display"])
        self.assertIsNone(session.draft)
        self.assertIsNone(session.catalogue)
        model.composition.assert_not_called()
        client.send.assert_not_called()

    def test_reply_after_new_draft_retains_selected_reply_to_and_headers(self):
        session, _client, _model = compose_session()
        session.handle("MAIL")
        session.handle("LIRE 1")
        session.handle("ECRIRE Alice : nouveau")
        result = session.handle("REPONDRE merci")
        self.assertIn("review_ticket", result)
        self.assertEqual(session.draft["kind"], "reply")
        self.assertEqual(session.draft["recipient"], "reply@example.org")
        parsed = BytesParser(policy=policy.default).parsebytes(session.draft["raw"])
        self.assertEqual(parsed["In-Reply-To"], "<original@example.org>")

    def test_cancel_uncertainty_and_benign_reply_error_text(self):
        session, client, _model = compose_session()
        result = session.handle("REPONDRE merci")
        self.assertNotIn("select_message_first", result["display"])
        self.assertIn("ECRIRE", result["display"])
        ticket = session.handle("ECRIRE Alice : mardi")["review_ticket"]
        session.acknowledge(ticket)
        client.send.side_effect = GmailError("send_uncertain")
        self.assertIn("incertain", session.handle("ENVOYER", ticket)["display"])
        session.handle("ENVOYER", ticket)
        client.send.assert_called_once()
        session.handle("ANNULER")
        self.assertIsNone(session.catalogue)


if __name__ == "__main__":
    unittest.main()
