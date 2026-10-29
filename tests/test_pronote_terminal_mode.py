"""Direct-mode integration through the actual terminal loop / key decoder."""
import threading
import unittest
from queue import Queue
from unittest.mock import Mock, patch

import test_terminal_startup as startup

from services import minitel_chatgpt as chat
from services import pronote_mode as mode
from services.console_terminal import Screen


class TerminalModeTests(unittest.TestCase):
    def setUp(self):
        from services.user_registry import Registry
        r=Registry()
        u=r.resolve('Test')
        r.configure(u.id,u.display_name,True,['Test'],None,False,['Alex','Sam'])

    def test_direct_output_stays_visible_while_waiting_for_next_command(self):
        cases = [
            (None, "NOTES / MOYENNES / DEVOIRS", b""),
            ({"records": {"child": "Alex", "items": [{"subject": "Maths", "start": "2026-10-14T08:00",
                                                         "end": "2026-10-14T09:00"}]}}, "Maths", b""),
            ({"records": {"error": "not_enrolled"}}, "PRONOTE: not_enrolled", b""),
            ({"display": "\n".join(f"Ligne {i}" for i in range(36))}, "Ligne 35", startup.fn(chat.K_SUITE)),
        ]
        for response, visible_text, paging in cases:
            with self.subTest(visible_text=visible_text):
                backend = Mock()
                backend.config.return_value = {"enabled": True, "children": {"Alex": {"name": "private"}}}

                def start(*args, response=response):
                    output = Queue()
                    output.put((response, None))
                    return Mock(is_alive=lambda: False), output, chat.time.monotonic()

                keys = startup.login_keys("fr")
                keys += b"PRONOTE" + startup.fn(chat.K_ENVOI)
                if response:
                    keys += b"COURS" + startup.fn(chat.K_ENVOI) + paging
                with (patch.object(chat.pronote, "backend", return_value=backend),
                      patch.object(chat, "start_pronote", side_effect=start) as request):
                    helper = startup.StartupTests()
                    helper.run_session(keys)
                # Inspect the screen at the next input wait, not the historical
                # byte stream: text that was emitted then cleared is not visible.
                screen = Screen()
                screen.write(helper.uart.output)
                rendered = "\n".join("".join(row) for row in screen.rows)
                self.assertIn(visible_text, rendered)
                self.assertIn("PRONOTE / AIDE / RETOUR", rendered)
                self.assertLessEqual(screen.row, 22)
                self.assertEqual(request.call_count, 1 if response else 0)

    def test_real_loop_routes_direct_commands_and_restores_normal_conversation(self):
        backend = Mock()
        backend.config.return_value = {"enabled": True, "children": {"Alex": {"name": "private"}, "Sam": {"name": "private2"}}}
        calls = []

        def start(request, school, language, options, knowledge):
            calls.append(request)
            output = Queue()
            output.put(({"records": {"child": request["arguments"]["child"], "items": [
                {"subject": "Maths", "start": "2026-10-14T08:00", "end": "2026-10-14T09:00"}],
                "retrieved_at": "2026-10-14T07:00"}}, None))
            return Mock(is_alive=lambda: False), output, chat.time.monotonic()

        keys = startup.login_keys("en")
        for text in ("PRONOTE", "1", "COURS", "ENFANT", "2", "COURS"):
            keys += text.encode() + startup.fn(chat.K_ENVOI)
        keys += startup.fn(chat.K_RETOUR) + b"Hello" + startup.fn(chat.K_ENVOI)
        with patch.object(chat.pronote, "backend", return_value=backend), patch.object(chat, "start_pronote", side_effect=start):
            helper = startup.StartupTests()
            screens, normal = helper.run_session(keys)
        self.assertEqual([c["arguments"]["child"] for c in calls], ["Alex", "Sam"])
        self.assertEqual(len(normal), 1)
        self.assertEqual(normal[0][1], [{"role": "user", "content": "Hello"}])
        self.assertTrue(any("PRONOTE / Alex" in screen for screen in screens))
        self.assertTrue(any("PRONOTE / Sam" in screen for screen in screens))
        self.assertTrue(any("Retour a la conversation" in screen for screen in screens))

    def test_pagination_only_suite_pages_and_retour_exits_mode(self):
        for function, expected in ((chat.K_RETOUR, "retour"), (chat.K_SOMMAIRE, "sommaire"), (chat.K_GUIDE, "guide")):
            with self.subTest(function=function):
                uart = startup.FakeSerial(startup.fn(function))
                term = chat.Term(transport=uart)
                self.assertEqual(chat.show_response(term, "\n".join(["x" * 39] * 40), allow_retour=True), expected)
        uart = startup.FakeSerial(startup.fn(chat.K_SUITE) * 3)
        self.assertEqual(chat.show_response(chat.Term(transport=uart), "\n".join(["x" * 39] * 40), allow_retour=True), "done")

    def test_one_grade_per_page_with_forward_and_backward_function_keys(self):
        backend = Mock()
        backend.config.return_value = {"enabled": True, "children": {"Alex": {"name": "private"}}}
        items = [{"subject": name, "grade": str(i + 10), "out_of": "20"}
                 for i, name in enumerate(("ALPHA", "BETA", "GAMMA"))]

        def start(*args):
            output = Queue()
            output.put(({"records": {"child": "Alex", "items": items}}, None))
            return Mock(is_alive=lambda: False), output, chat.time.monotonic()

        keys = startup.login_keys("fr")
        for text in ("PRONOTE", "NOTES"):
            keys += text.encode() + startup.fn(chat.K_ENVOI)
        keys += startup.fn(chat.K_SUITE) + startup.fn(chat.K_RETOUR)
        keys += b"SUIVANT" + startup.fn(chat.K_ENVOI) + startup.fn(chat.K_SUITE) * 2
        for text in ("QUITTER", "Hello"):
            keys += text.encode() + startup.fn(chat.K_ENVOI)
        with patch.object(chat.pronote, "backend", return_value=backend), patch.object(chat, "start_pronote", side_effect=start) as request:
            screens, normal = startup.StartupTests().run_session(keys)
        pages = [s for s in screens if any(f"Note {i}/3" in s for i in range(1, 4))]
        expected = ("ALPHA", "BETA", "ALPHA", "BETA", "GAMMA", "GAMMA")
        self.assertEqual(len(pages), len(expected))
        for page, subject in zip(pages, expected):
            self.assertIn(subject, page)
            self.assertEqual(sum(name in page for name in ("ALPHA", "BETA", "GAMMA")), 1)
        request.assert_called_once()
        self.assertEqual(len(normal), 1)
        self.assertEqual(normal[0][1], [{"role": "user", "content": "Hello"}])

    def test_long_grade_stays_on_one_physical_page_with_complete_detail_available(self):
        backend = Mock()
        backend.config.return_value = {"enabled": True, "children": {"Alex": {"name": "private"}}}
        item = {"subject": "Œuvre très longue " * 10, "comment": "Commentaire " * 30,
                "grade": "Non note " * 5, "out_of": "20", "class_average": "12,5 " * 8,
                "minimum": "0 " * 20, "maximum": "20 " * 13, "coefficient": "1 " * 20}

        def start(*args):
            output = Queue()
            output.put(({"records": {"child": "Alex", "items": [item]}}, None))
            return Mock(is_alive=lambda: False), output, chat.time.monotonic()

        keys = startup.login_keys("fr")
        for text in ("PRONOTE", "NOTES"):
            keys += text.encode() + startup.fn(chat.K_ENVOI)
        with patch.object(chat.pronote, "backend", return_value=backend), patch.object(chat, "start_pronote", side_effect=start):
            helper = startup.StartupTests()
            helper.run_session(keys)
        screen = Screen()
        screen.write(helper.uart.output)
        visible = "\n".join("".join(row) for row in screen.rows)
        self.assertIn("Note 1/1", visible)
        self.assertIn("LIRE 1: detail", visible)
        self.assertIn("RETOUR:prec", visible)
        self.assertLessEqual(screen.row, 22)

    def test_typed_retour_on_a_page_restores_normal_conversation(self):
        backend = Mock()
        backend.config.return_value = {"enabled": True, "children": {"Alex": {"name": "private"}}}

        def start(*args):
            output = Queue()
            output.put(({"display": "\n".join(f"Cours {i}" for i in range(36))}, None))
            return Mock(is_alive=lambda: False), output, chat.time.monotonic()

        keys = startup.login_keys("fr")
        for text in ("PRONOTE", "COURS", "retour", "Hello"):
            keys += text.encode() + startup.fn(chat.K_ENVOI)
        with patch.object(chat.pronote, "backend", return_value=backend), patch.object(chat, "start_pronote", side_effect=start) as school:
            _, normal = startup.StartupTests().run_session(keys)
        self.assertEqual(school.call_count, 1)
        self.assertEqual(len(normal), 1)
        self.assertEqual(normal[0][1], [{"role": "user", "content": "Hello"}])

    def test_typed_exit_while_waiting_does_not_become_a_school_request(self):
        for text in (b"RETOUR", b"quitter", b"SORTIX\x08R"):
            with self.subTest(text=text):
                pending = (Mock(is_alive=lambda: True), Queue(), chat.time.monotonic())
                term = chat.Term(transport=startup.FakeSerial(text + startup.fn(chat.K_ENVOI)))
                self.assertEqual(chat.wait_answer(term, pending, 30, allow_retour=True), (None, "retour"))

    def test_exit_forgets_private_school_context_but_keeps_public_conversation(self):
        backend = Mock()
        backend.config.return_value = {"enabled": True, "children": {"Alex": {"name": "private"}}}
        seen = []

        def answer(history, *args, **kwargs):
            seen.append([dict(t) for t in history])
            self.assertIs(kwargs.get("pronote"), backend)
            private = history[-1]["content"] == "Notes de Alex"
            return {"status": "ordinary", "text": "PRIVATE RECORD" if private else "Public answer",
                    "chunks": [], "references": [], "private": private}

        keys = startup.login_keys("fr")
        for text in ("Soleil", "Notes de Alex", "PRONOTE", "RETOUR", "Hello"):
            keys += text.encode() + startup.fn(chat.K_ENVOI)
        with patch.object(chat.pronote, "backend", return_value=backend):
            startup.StartupTests().run_session(keys, factual_reply=answer)
        self.assertEqual(len(seen), 3)
        self.assertEqual([t["content"] for t in seen[-1]], ["Soleil", "Public answer", "Hello"])
        self.assertFalse(any(t.get("private") for t in seen[-1]))

    def test_full_print_content_does_not_use_last_displayed_page(self):
        backend = Mock()
        backend.config.return_value = {"enabled": True, "children": {"Alex": {"name": "private"}}}
        session = mode.Session(backend)
        session.prepare("PRONOTE")
        session.receive({"child": "Alex", "items": [{"subject": "Maths", "description": f"Exercice {i}"} for i in range(25)]}, "homework")
        printable = session.prepare("IMPRIMER")
        self.assertTrue(printable["print"])
        self.assertIn("Exercice 0", printable["display"])
        self.assertIn("Exercice 24", printable["display"])

    def test_real_sdk_period_and_announcement_contracts(self):
        from pronotepy.dataClasses import Information, Period

        from services import pronote_records as records

        client = Mock()
        client.info.id = "synthetic-parent"
        period = Period(client, {"N": "period", "L": "Trimestre 1", "dateDebut": {"V": "01/09/2026 00:00:00"},
                                 "dateFin": {"V": "31/12/2026 00:00:00"}})
        self.addCleanup(Period.instances.clear)
        client.periods = [period]
        client.post.return_value = {"dataSec": {"data": {"listeServices": {"V": [
            {"N": "maths", "L": "Maths", "moyEleve": {"V": "12,5"}, "baremeMoyEleve": {"V": "20"},
             "moyClasse": {"V": "11"}, "moyMin": {"V": "2"}, "moyMax": {"V": "19"}}]}}}}
        items, _ = records.collect(client, "averages", {"period": "Trimestre 1"})
        self.assertEqual(items[0]["student_average"], "12,5")
        self.assertEqual(client.post.call_args.args[:2], ("DernieresNotes", 198))
        client.post.return_value = {"dataSec": {"data": {"Message": "Non publie"}}}
        self.assertEqual(records.collect(client, "report", {"period": "Trimestre 1"})[1]["error"], "report_unavailable")
        self.assertEqual(client.post.call_args.args[:2], ("PageBulletins", 13))
        information = Information(client, {"N": "synthetic-announcement", "L": "Annonce", "auteur": "Ecole", "lue": False,
                                           "dateCreation": {"V": "14/10/2026 00:00:00"}, "nature": {"V": {"L": "Info"}},
                                           "estSondage": False, "reponseAnonyme": False})
        client.post.return_value = {"dataSec": {"data": {"detailsActualite": {"listeQuestions": {"V": [
            {"texte": {"V": "<p>Bonjour</p>"}, "listePiecesJointes": {"V": []}}]}}}}}
        self.assertIn("Bonjour", information.content())
        self.assertEqual(information.attachments(), [])
        self.assertFalse(information.read)
        self.assertEqual(client.post.call_args.args[0], "PageActualites")
        self.assertEqual(client.post.call_args.args[2]["genreRequeteActualite"], 1)

    def test_cancelled_direct_worker_cannot_fill_a_new_session(self):
        release, entered = threading.Event(), threading.Event()
        backend = Mock()

        def blocked(*args):
            entered.set()
            release.wait(2)
            return {"child": "Alex", "items": [{"description": "STALE"}]}

        backend.execute.side_effect = blocked
        pending = chat.start_pronote({"tool": "pronote_get_homework", "arguments": {"child": "Alex"}},
                                     backend, "fr", ("conversations", False, 30), "")
        try:
            self.assertTrue(entered.wait(1))
            term = chat.Term(transport=startup.FakeSerial(startup.fn(chat.K_RETOUR)))
            self.assertEqual(chat.wait_answer(term, pending, 30, allow_retour=True), (None, "retour"))
            session = mode.Session(backend)
            self.assertIsNone(session.last)
        finally:
            release.set()
            pending[0].join(2)
        self.assertIsNone(session.last)
        self.assertEqual(term.s.output, b"")

    def test_missing_llm_key_returns_help_while_direct_tools_still_run(self):
        backend = Mock()
        backend.execute.return_value = {"items": []}
        with patch.object(chat, "MISTRAL_KEY", ""), patch.object(chat, "PROVIDER", "mistral"):
            pending = chat.start_pronote({"fallback": "Resumer", "child": "Alex"}, backend, "fr", ("conversations", False, 30), "")
            pending[0].join(2)
            self.assertIn("Utilisez AIDE", pending[1].get_nowait()[0]["display"])
            pending = chat.start_pronote({"tool": "pronote_get_grades", "arguments": {"child": "Alex"}},
                                         backend, "fr", ("conversations", False, 30), "")
            pending[0].join(2)
            self.assertEqual(pending[1].get_nowait()[0]["records"], {"items": []})
        backend.execute.assert_called_once()

    def test_private_normal_school_turns_never_enter_persistent_memory(self):
        uart = startup.FakeSerial(startup.login_keys("fr")
                                  + b"Qu en est il" + startup.fn(chat.K_ENVOI)
                                  + b"Hello" + startup.fn(chat.K_ENVOI))
        memory = Mock(take_warning=lambda: "")
        memory.saved_language.return_value = None
        backend = Mock()
        backend.config.return_value = {"enabled": True, "children": {"Alex": {}}}

        def start(system, history, *args, **kwargs):
            private = history[-1]["content"] != "Hello"
            text = "PRIVATE SCHOOL RECORD" if private else "Public answer"
            result = {"display": text, "turn": {"role": "assistant", "content": text, "private": private}}
            output = Queue()
            output.put((result, None))
            return Mock(is_alive=lambda: False), output, chat.time.monotonic()

        with (patch.object(chat, "select_memory_user", return_value=(memory, "envoi")),
              patch.object(chat, "start_answer", side_effect=start), patch.object(chat, "MISTRAL_KEY", ""),
              patch.object(chat, "load_preset", return_value=("persona", "3615 IA", "Question?", "WAIT", None)),
              patch.object(chat, "build_title", return_value=["LOGO"]), patch.object(chat.pronote, "backend", return_value=backend),
              patch.object(chat.gmail_mail, "enabled", return_value=False), self.assertRaises(startup.EndOfInput)):
            chat.run(chat.Term(transport=uart))
        calls = memory.record.call_args_list
        self.assertNotIn("PRIVATE", str(calls))
        self.assertNotIn("Qu en est il", str(calls))
        self.assertTrue(any(c.args[:2] == ("user", "Hello") for c in calls))

    def test_unusual_names_use_existing_safe_character_boundary(self):
        source = mode.render({"child": "Alex", "items": [{"subject": "Mathématiques—Œuvre d’O’Neil " * 5,
                                                              "description": "Été : x ≤ 3 ; Ω"}]}, "homework", full=True)
        lines = chat.wrap(chat.safe_display(source))
        self.assertTrue(all(len(line) <= 39 for line in lines))
        self.assertIn("Mathematiques", "\n".join(lines))
        self.assertNotIn("?", "\n".join(lines))
