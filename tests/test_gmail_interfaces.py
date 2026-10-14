"""Flask and simulated UART checks, with no live email calls."""

import unittest
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import test_terminal_startup as startup
from test_gmail import make_session
from test_gmail_composition import compose_session
from test_terminal_startup import fn

from services import admin_ui as admin
from services import gmail_mail as mail
from services import minitel_chatgpt as terminal
from services import memory_session


class RecipientAddressRoutingTests(unittest.TestCase):
    def test_explicit_modify_keeps_note_as_edit_instruction_instead_of_memory(self):
        with TemporaryDirectory() as root:
            session, client, model = compose_session()
            session.handle("ECRIRE Alice : confirmer mardi")
            session.handle("MODIFIER")
            selected = memory_session.MemorySession("test", root=root, model=Mock())
            try:
                with patch.dict("os.environ", {"GMAIL_ENABLED": "1"}):
                    result = terminal.call_answer("persona", [{"role": "user", "content": "Note Camille comme signature"}],
                        "fr", ("conversations", True, 60), "", mail=session, memory=selected)
                self.assertIn("review_ticket", result)
                self.assertEqual(model.composition.call_args.args[0], "Note Camille comme signature")
                selected.model.plan.assert_not_called()
                model.route.assert_not_called()
                client.send.assert_not_called()
            finally:
                selected.close()

    def test_pending_email_address_wins_over_memory_keywords(self):
        for address in ("note@example.net", "saved@example.net", "notes@school.example"):
            with self.subTest(address=address), TemporaryDirectory() as root:
                session, client, model = compose_session()
                model.resolve.side_effect = None
                model.resolve.return_value = {"status": "no_match", "ids": []}
                session.handle("ECRIRE Bob : confirmer mardi")
                memory_model = Mock()
                selected = memory_session.MemorySession("test", root=root, model=memory_model)
                try:
                    with patch.dict("os.environ", {"GMAIL_ENABLED": "1"}):
                        result = terminal.call_answer("persona", [{"role": "user", "content": address}],
                            "fr", ("conversations", True, 60), "", mail=session, memory=selected)
                    self.assertIn("review_ticket", result)
                    self.assertEqual(session.draft["recipient"], address)
                    memory_model.plan.assert_not_called()
                    client.send.assert_not_called()
                finally:
                    selected.close()


class AdminMailTests(unittest.TestCase):
    def setUp(self):
        admin.MAIL_SESSIONS.clear()
        self.temp = TemporaryDirectory()
        self.memory_env = patch.dict("os.environ", {"MINITEL_MEMORY_DIR": self.temp.name})
        self.memory_env.start()
        self.client = admin.app.test_client()
        with self.client.session_transaction() as cookie:
            cookie["logged_in"] = True
        self.enabled = patch.dict("os.environ", {"GMAIL_ENABLED": "1"})
        self.enabled.start()
        self.mail, self.api, self.model = make_session(
            output="Long complete reply. " * 90
        )
        self.factory = patch.object(
            admin.gmail_mail, "MailSession", return_value=self.mail
        )
        self.factory.start()
        self.client.get("/gmail")
        with self.client.session_transaction() as cookie:
            self.csrf = cookie["gmail_csrf"]

    def tearDown(self):
        for identifier in list(admin.MEMORY_SESSIONS):
            admin.discard_memory(identifier)
        self.factory.stop()
        self.enabled.stop()
        self.memory_env.stop()
        self.temp.cleanup()
        admin.MAIL_SESSIONS.clear()

    def command(self, question, ticket=""):
        return self.client.post(
            "/gmail",
            data={
                "csrf": self.csrf,
                "action": "command",
                "question": question,
                "ticket": ticket,
            },
        )

    def make_draft(self):
        self.command("MAIL")
        self.command("LIRE 1")
        return self.command("REPONDRE remercier")

    def test_authentication_csrf_and_mail_never_in_cookie(self):
        self.assertEqual(admin.app.test_client().get("/gmail").status_code, 302)
        self.assertEqual(
            self.client.post("/gmail", data={"action": "send"}).status_code, 403
        )
        self.make_draft()
        with self.client.session_transaction() as cookie:
            encoded = str(dict(cookie))
        self.assertNotIn("Long complete reply", encoded)
        self.assertNotIn("reply@example.org", encoded)
        self.assertNotIn("fake-refresh", encoded)
        response = self.client.get("/gmail")
        self.assertIn("no-store", response.headers["Cache-Control"])

    def test_all_pages_reviewed_before_sending_and_paging_has_no_api_or_llm(self):
        response = self.make_draft()
        item = next(iter(admin.MAIL_SESSIONS.values()))
        ticket = item["ticket"]
        self.assertGreater(len(item["pages"]), 1)
        self.assertFalse(self.mail.review_valid(ticket))
        self.assertRegex(response.text, r'value="send" disabled')
        api_calls, model_calls = self.api.method_calls[:], self.model.method_calls[:]
        for _ in range(len(item["pages"]) - 1):
            response = self.client.post(
                "/gmail", data={"csrf": self.csrf, "action": "next"}
            )
        self.assertEqual(self.api.method_calls, api_calls)
        self.assertEqual(self.model.method_calls, model_calls)
        self.assertTrue(self.mail.review_valid(ticket))
        # The longer hardware signature wraps at the terminal's 39-column width.
        self.assertIn(mail.SIGNATURE, " ".join(" ".join(item["pages"]).split()))
        self.assertNotRegex(response.text, r'value="send" disabled')
        self.command("ENVOYER", ticket)
        self.api.send.assert_called_once()
        self.command("ENVOYER", ticket)
        self.api.send.assert_called_once()

    def test_forged_send_before_last_page_and_stale_revision_fail(self):
        self.make_draft()
        item = next(iter(admin.MAIL_SESSIONS.values()))
        ticket = item["ticket"]
        result = self.command("ENVOYER", ticket)
        self.assertIn("review_current_draft_first", result.text)
        self.api.send.assert_not_called()
        self.command("MODIFIER shorter")
        new_ticket = item["ticket"]
        self.assertNotEqual(ticket, new_ticket)
        self.mail.acknowledge(ticket)
        self.assertFalse(self.mail.review_valid(new_ticket))
        self.command("ENVOYER", ticket)
        self.api.send.assert_not_called()

    def test_escaping_reset_logout_and_bounded_sessions(self):
        self.api.list.return_value[0]["subject"] = '<script>alert("evil")</script>'
        response = self.command("MAIL")
        self.assertNotIn('<script>alert("evil")</script>', response.text)
        self.assertIn("&lt;script&gt;", response.text)
        self.make_draft()
        self.client.post("/gmail", data={"csrf": self.csrf, "action": "reset"})
        self.assertIsNone(self.mail.draft)
        self.assertEqual(self.mail.items, [])
        self.make_draft()
        self.client.get("/logout")
        self.assertIsNone(self.mail.draft)
        self.assertFalse(admin.MAIL_SESSIONS)

    def test_new_composition_review_revision_paging_and_confirmed_send(self):
        self.factory.stop()
        self.mail, self.api, self.model = compose_session()
        self.model.composition.return_value = ("Nouveau sujet", "Bonjour. " * 150)
        self.factory = patch.object(
            admin.gmail_mail, "MailSession", return_value=self.mail
        )
        self.factory.start()
        admin.MAIL_SESSIONS.clear()
        self.client.get("/gmail")
        response = self.command("ECRIRE Alice : confirmer mardi")
        item = next(iter(admin.MAIL_SESSIONS.values()))
        ticket = item["ticket"]
        self.assertGreater(len(item["pages"]), 1)
        self.assertIn("alice@example.org", response.text)
        self.assertNotRegex(response.text, r'value="send"(?! disabled)')
        api_calls, model_calls = self.api.method_calls[:], self.model.method_calls[:]
        while item["page"] < len(item["pages"]) - 1:
            self.client.post("/gmail", data={"csrf": self.csrf, "action": "next"})
        self.assertEqual(api_calls, self.api.method_calls)
        self.assertEqual(model_calls, self.model.method_calls)
        self.assertTrue(self.mail.review_valid(ticket))
        self.model.composition.return_value = ("Objet révisé", "Confirmation mardi.")
        self.command("MODIFIER objet et raccourcir")
        newer = item["ticket"]
        self.assertNotEqual(ticket, newer)
        self.command("ENVOYER", ticket)
        self.api.send.assert_not_called()
        # Reread after a failed stale-ticket request; its output replaced the preview.
        self.command("MODIFIER conserver le texte")
        self.assertEqual(len(item["pages"]), 1)
        newer = item["ticket"]
        self.command("ENVOYER", newer)
        self.api.send.assert_called_once()
        self.assertIsNone(self.api.send.call_args.args[1])
        self.command("ENVOYER", newer)
        self.api.send.assert_called_once()
        with self.client.session_transaction() as cookie:
            self.assertNotIn("alice@example.org", str(dict(cookie)))


class TerminalMailTests(unittest.TestCase):
    def test_new_draft_natural_route_and_uart_review_remain_private(self):
        session, client, model = compose_session()
        model.route.return_value = {
            "action": "compose",
            "recipient": "Alice",
            "instruction": "confirmer mardi",
        }
        model.composition.return_value = ("Objet neuf", "Bonjour. " * 120)
        with (
            patch.dict("os.environ", {"GMAIL_ENABLED": "1"}),
            patch.object(terminal.mistral_answers, "answer") as public,
        ):
            result = terminal.call_answer(
                "persona",
                [
                    {
                        "role": "user",
                        "content": "Écris un mail à Alice pour confirmer mardi",
                    }
                ],
                "fr",
                ("conversations", True, 60),
                "knowledge",
                session,
            )
            self.assertIsNone(result["turn"])
            public.assert_not_called()
        text = terminal.safe_display(result["display"])
        term = object.__new__(terminal.Term)
        term.s = startup.FakeSerial(fn(terminal.K_SOMMAIRE))
        self.assertEqual(terminal.show_response(term, text), "sommaire")
        self.assertFalse(session.review_valid(result["review_ticket"]))
        before = (client.method_calls[:], model.method_calls[:])
        term.s = startup.FakeSerial(fn(terminal.K_SUITE) * (len(mail.pages(text)) - 1))
        self.assertEqual(terminal.show_response(term, text), "done")
        self.assertEqual(before, (client.method_calls, model.method_calls))
        session.acknowledge(result["review_ticket"])
        session.handle("ENVOYER", result["review_ticket"])
        client.send.assert_called_once()
        self.assertIsNone(client.send.call_args.args[1])

    def test_gmail_router_is_private_and_nonmail_still_uses_existing_engine(self):
        session, _client, _ = make_session()
        with patch.dict("os.environ", {"GMAIL_ENABLED": "1"}):
            with patch.object(terminal.mistral_answers, "answer") as public:
                result = terminal.call_answer(
                    "persona",
                    [{"role": "user", "content": "MAIL"}],
                    "fr",
                    ("conversations", True, 60),
                    "knowledge",
                    session,
                )
                self.assertIsNone(result["turn"])
                public.assert_not_called()
                public.return_value = {
                    "status": "ordinary",
                    "text": "Paris",
                    "chunks": [],
                    "references": [],
                }
                terminal.call_answer(
                    "persona",
                    [{"role": "user", "content": "What is France's capital?"}],
                    "en",
                    ("conversations", True, 60),
                    "knowledge",
                    session,
                )
                public.assert_called_once()
            self.assertTrue(session.interested("LIRE 1"))
            session.handle("LIRE 1")
            result = session.handle("REPONDRE oui")
            self.assertTrue(session.interested("Make it shorter"))
            self.assertTrue(session.interested("Peux-tu lui répondre oui"))
            self.assertNotIn("\\u2014", terminal.safe_display(result["display"]))

    def test_simulated_physical_review_pages_signature_send_and_sommaire(self):
        session, client, _ = make_session(output="Merci. " * 120)
        session.handle("MAIL")
        session.handle("LIRE 1")
        result = session.handle("REPONDRE oui")
        ticket = result["review_ticket"]
        text = terminal.safe_display(result["display"])
        page_count = len(mail.pages(text))
        term = object.__new__(terminal.Term)
        uart = startup.FakeSerial(fn(terminal.K_SOMMAIRE))
        term.s = uart
        self.assertEqual(terminal.show_response(term, text), "sommaire")
        self.assertFalse(session.review_valid(ticket))
        client.send.assert_not_called()
        uart = startup.FakeSerial(fn(terminal.K_SUITE) * (page_count - 1))
        term.s = uart
        self.assertEqual(terminal.show_response(term, text), "done")
        session.acknowledge(ticket)
        self.assertIn(
            "Envoye par Minitel 722079V (138121)", startup.screen_text(uart.output)
        )
        session.handle("ENVOYER", ticket)
        client.send.assert_called_once()


class FullTerminalWorkflow(unittest.TestCase):
    def test_real_loop_binds_review_ticket_and_clears_on_sommaire(self):
        from queue import Queue
        from unittest.mock import Mock

        session, api, _ = make_session()
        uart = startup.FakeSerial(
            b"fr"
            + fn(terminal.K_ENVOI)
            + fn(terminal.K_SUITE)
            + b"MAIL"
            + fn(terminal.K_ENVOI)
            + b"LIRE 1"
            + fn(terminal.K_ENVOI)
            + b"REPONDRE oui"
            + fn(terminal.K_ENVOI)
            + b"ENVOYER"
            + fn(terminal.K_ENVOI)
            + b"ENVOYER"
            + fn(terminal.K_ENVOI)
            + fn(terminal.K_SOMMAIRE)
        )

        def synchronous(system, history, language, options, knowledge, **kwargs):
            result = terminal.call_answer(
                system, history, language, options, knowledge, **kwargs
            )
            queue = Queue()
            queue.put((result, None))
            return Mock(is_alive=lambda: False), queue, terminal.time.monotonic()

        with (
            patch.object(terminal, "select_memory_user", return_value=(Mock(take_warning=lambda: "", interested=lambda q: False), "envoi")),
            patch.dict("os.environ", {"GMAIL_ENABLED": "1"}),
            patch.object(terminal.gmail_mail, "MailSession", return_value=session),
            patch.object(terminal.serial, "Serial", return_value=uart),
            patch.object(terminal.time, "sleep"),
            patch.object(
                terminal,
                "load_preset",
                return_value=("persona", "3615 IA", "Question?", "WAIT", None),
            ),
            patch.object(terminal, "build_title", return_value=["LOGO"]),
            patch.object(
                terminal, "prepare_boot", return_value=terminal.UI["fr"]["boot"]
            ),
            patch.object(terminal, "factual_knowledge", return_value=""),
            patch.object(terminal, "start_answer", side_effect=synchronous),
            patch.object(terminal.mistral_answers, "answer") as public,
            self.assertRaises(startup.EndOfInput),
        ):
            terminal.run()
        api.send.assert_called_once()
        public.assert_not_called()
        self.assertIsNone(session.draft)
        self.assertEqual(session.loaded, {})
        screens = startup.screen_text(uart.output)
        self.assertIn("Gmail confirme", screens)
        self.assertIn("review_current_draft_first", screens)


class GeneralEmailQuestions(unittest.TestCase):
    def test_general_email_facts_retain_existing_web_answer_path(self):
        session, _client, model = make_session()
        model.route.return_value = {"action": "public"}
        with (
            patch.dict("os.environ", {"GMAIL_ENABLED": "1"}),
            patch.object(terminal.mistral_answers, "answer") as public,
        ):
            public.return_value = {
                "status": "ordinary",
                "text": "Gmail is Google's email service.",
                "chunks": [],
                "references": [],
            }
            result = terminal.call_answer(
                "persona",
                [{"role": "user", "content": "What is Gmail?"}],
                "en",
                ("conversations", True, 60),
                "",
                session,
            )
        public.assert_called_once()
        self.assertIsNotNone(result["turn"])


if __name__ == "__main__":
    unittest.main()
