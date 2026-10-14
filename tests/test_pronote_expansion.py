"""Synthetic pinned-SDK projections, scope/limits and terminal vocabulary."""
import json
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace as Obj
from unittest.mock import Mock, patch

import requests
from pronotepy.exceptions import UnsupportedOperation

from services import minitel_chatgpt as chat
from services import pronote as pn
from services import pronote_mode as mode
from services import pronote_records as records


class ExpansionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.factory = Mock()
        self.backend = pn.Pronote(Path(self.temp.name), lambda: self.factory)
        self.mapping = {"Alex": {"account": "family", "name": "PRIVATE NAME"},
                        "Sam": {"account": "family", "name": "OTHER PRIVATE NAME"}}
        self.backend.configure(True, self.mapping)
        self.creds = {"uuid": "device", "password": "PRIVATE TOKEN"}
        pn.atomic_json(self.backend.credentials_path("family"), self.creds)
        self.day = pn.today()
        now = datetime.combine(self.day, datetime.min.time())
        subject = Obj(name="Mathématiques—O’Neil")
        self.period = Obj(name="Trimestre 1", start=now - timedelta(days=20), end=now + timedelta(days=20),
                          grades=[Obj(date=self.day, subject=subject, grade="Non noté", out_of="20", coefficient="0,5",
                                      comment="Bravo", average="11,2", min="0", max="19", is_bonus=True, is_optionnal=False)],
                          averages=[Obj(subject=subject, student="12,5", class_average="11", min="2", max="19", out_of="20")],
                          report=Obj(subjects=[Obj(name=subject.name, student_average="12,5", class_average="11",
                                                  min_average="2", max_average="19", coefficient="1",
                                                  comments=["Très bien"], teachers=["Professeur"])], comments=["Encouragements"]),
                          evaluations=[Obj(name="Fractions", date=self.day, teacher="Professeur", coefficient=1,
                                           subject=subject, description="Évaluation", acquisitions=[Obj(name="Calcul", domain="Nombres",
                                                                                                      level="Acquis", abbreviation="A")])],
                          absences=[Obj(from_date=now, to_date=now + timedelta(hours=2), hours="2", days=0, justified=False, reasons=["Maladie"])],
                          delays=[Obj(date=now, minutes=5, justified=False, justification=None, reasons=[])],
                          punishments=[Obj(given=now, nature="Retenue", circumstances="Motif", homework=None, duration=timedelta(hours=1),
                                           reasons=["Incident"], schedule=[Obj(start=now + timedelta(days=2), duration=timedelta(hours=1))])])
        self.client = Mock(logged_in=True)
        self.client.children = [Obj(name=m["name"], id=a) for a, m in self.mapping.items()]
        self.client.periods = [self.period]
        self.client.export_credentials.return_value = self.creds
        self.lessons = [Obj(start=now + timedelta(hours=8), end=now + timedelta(hours=9), subject=subject,
                            teacher_name="Professeur", classroom="A1", canceled=False, status="Modifie", test=True)]
        self.client.lessons.return_value = self.lessons
        self.client.homework.return_value = [Obj(date=self.day, subject=subject, done=False, description="Exercice <b>2</b>")]
        self.notice = Obj(title="Information", author="École", creation_date=now, start_date=None, category="Vie scolaire",
                          read=False, survey=True, content=Mock(return_value="Bonjour https://private.example/token"),
                          attachments=Mock(return_value=[Obj(name="fiche.pdf", type=1, url="PRIVATE URL"),
                                                         Obj(name="https://private.example/token", type=0)]),
                          mark_as_read=Mock(), answer=Mock())
        self.client.information_and_surveys.return_value = [self.notice]
        self.client.menus.return_value = [Obj(date=self.day, name="Menu", is_lunch=True, is_dinner=False,
                                            main_meal=[Obj(name="Poisson")])]
        self.factory.token_login.return_value = self.client

    def execute(self, operation, **args):
        return self.backend.execute("pronote_get_" + operation, {"child": "Alex", **args})

    def test_all_new_tools_and_existing_grades_have_sanitized_records(self):
        operations = list(records.PERIOD_OPERATIONS) + list(records.DATE_OPERATIONS) + ["grades"]
        offered = {tool["function"]["name"] for tool in self.backend.tools()}
        for op in operations:
            with self.subTest(op=op):
                self.assertIn("pronote_get_" + op, offered)
                result = self.execute(op)
                self.assertNotIn("error", result)
                self.assertTrue(result["items"])
                self.assertNotIn("PRIVATE", json.dumps(result))
                self.assertTrue(all(len(line) <= 39 for line in mode.render(result, op).splitlines()))
        grades = self.execute("grades")["items"][0]
        self.assertEqual(grades["grade"], "Non noté")
        self.assertEqual(grades["minimum"], "0")
        self.assertTrue(grades["bonus"])
        self.assertEqual(self.execute("averages")["items"][0]["student_average"], "12,5")
        self.assertEqual(self.execute("evaluations")["items"][0]["acquisitions"][0]["level"], "Acquis")

    def test_notices_do_not_write_or_return_urls_and_details_are_bounded(self):
        self.client.information_and_surveys.return_value = [self.notice] * 12
        result = self.execute("announcements")
        self.assertEqual(self.notice.content.call_count, 5)
        self.assertEqual(result["items"][5]["content_error"], "detail_limit")
        self.assertEqual(result["items"][0]["attachments"], [{"filename": "fiche.pdf", "type": "file"}])
        self.assertNotIn("https://", json.dumps(result))
        self.notice.mark_as_read.assert_not_called()
        self.notice.answer.assert_not_called()

    def test_unpublished_report_and_wrong_period_never_substitute(self):
        self.period.report = None
        self.assertEqual(self.execute("report")["error"], "report_unavailable")
        self.assertEqual(self.execute("report", period="Wrong")["error"], "select_period")
        self.assertNotIn("error", self.execute("absences", period="Trimestre 1"))

    def test_date_bounds_and_child_allowlist_for_every_operation_before_login(self):
        for op in list(records.PERIOD_OPERATIONS) + list(records.DATE_OPERATIONS):
            with self.subTest(op=op):
                self.assertEqual(self.backend.execute("pronote_get_" + op, {"child": "PRIVATE NAME"})["error"], "unknown_child")
        for op in records.DATE_OPERATIONS:
            self.assertEqual(self.execute(op, **{"from": "2026-02-30"})["error"], "invalid_arguments")
            self.assertEqual(self.execute(op, **{"from": "2026-01-01", "to": "2026-03-01"})["error"], "invalid_date_range")
        self.factory.token_login.assert_not_called()

    def test_empty_and_malformed_data_are_distinct(self):
        self.period.averages = []
        self.assertEqual(self.execute("averages")["items"], [])
        self.backend.cache.clear()
        self.period.averages = [Obj(subject=None, student=object())]
        self.assertEqual(self.execute("averages")["error"], "malformed_response")

    def test_briefing_one_login_partial_failure_and_child_scope(self):
        self.client.homework.side_effect = UnsupportedOperation("PRIVATE SCHOOL MESSAGE")
        result = self.execute("briefing")
        self.factory.token_login.assert_called_once()
        self.client.set_child.assert_called_once_with(self.client.children[0])
        self.assertTrue(result["partial"])
        self.assertEqual(result["items"][2]["error"], "feature_unavailable")
        self.assertTrue(result["items"][0]["items"])
        self.client.lessons.assert_called_once()
        self.assertNotIn("PRIVATE", json.dumps(result))

    def test_canceled_tests_excluded_and_limit_declared(self):
        self.lessons[0].canceled = True
        self.assertEqual(self.execute("tests")["items"], [])
        self.backend.cache.clear()
        self.lessons[0].canceled = False
        self.client.lessons.return_value *= 101
        result = self.execute("tests")
        self.assertTrue(result["truncated"])
        self.assertLessEqual(len(json.dumps(result["items"])), pn.MAX_RESULT_CHARACTERS)

    def test_failure_codes_no_sensitive_exception_messages(self):
        for error, code in [(UnsupportedOperation("SECRET"), "feature_unavailable"),
                            (requests.Timeout("SECRET"), "timeout"), (KeyError("SECRET"), "malformed_response")]:
            self.client.menus.side_effect = error
            self.assertEqual(self.execute("menus")["error"], code)

    def test_concurrent_new_reads_are_serialized_and_do_not_share_child(self):
        selected, outputs = [], []
        self.client.set_child.side_effect = lambda c: selected.append(c.id)

        def menus(*args):
            time.sleep(.01)
            return [Obj(date=self.day, name=selected[-1], is_lunch=True, is_dinner=False)]

        self.client.menus.side_effect = menus
        threads = [threading.Thread(target=lambda a=a: outputs.append(self.backend.execute("pronote_get_menus", {"child": a})))
                   for a in ("Alex", "Sam")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(2)
        self.assertEqual({r["child"]: r["items"][0]["name"] for r in outputs}, {"Alex": "Alex", "Sam": "Sam"})

    def test_direct_entry_selection_every_command_and_exit(self):
        session = mode.Session(self.backend)
        self.assertIsNone(session.prepare("Hello"))
        self.assertIn("Choisir", session.prepare("pronote")["display"])
        session.prepare("2")
        self.assertEqual(session.child, "Sam")
        for command, op in mode.COMMANDS.items():
            request = session.prepare(command.lower())
            self.assertEqual(request["tool"], "pronote_get_" + op)
            self.assertEqual(request["arguments"]["child"], "Sam")
        session.prepare("RETOUR")
        self.assertFalse(session.mode)
        self.assertIsNone(session.prepare("NOTES"))

    def test_context_dates_accents_subject_period_and_detail_views(self):
        session = mode.Session(self.backend)
        session.prepare("PRONOTE")
        session.prepare("Alex")
        session.prepare("cours")
        self.assertEqual(session.prepare("demain")["arguments"]["from"], (self.day + timedelta(days=1)).isoformat())
        week = session.prepare("semaine")["arguments"]
        self.assertEqual((pn.date.fromisoformat(week["to"]) - pn.date.fromisoformat(week["from"])).days, 6)
        self.assertEqual(session.prepare("contrôles")["operation"], "tests")
        self.assertEqual(session.prepare("BULLETIN Trimestre 1")["arguments"]["period"], "Trimestre 1")
        session.receive(self.execute("grades"), "grades")
        self.assertIn("Non noté", session.prepare("mathématiques")["display"])
        session.receive(self.execute("announcements"), "announcements")
        self.assertNotIn("Bonjour", session.prepare("AIDE")["display"])
        self.assertIn("Bonjour", session.prepare("LIRE 1")["display"])
        self.assertTrue(session.prepare("IMPRIMER")["print"])
        self.assertIn("Bonjour", session.prepare("TEXTE")["display"])
        self.assertIn("indisponible", session.prepare("LIRE 999")["display"])

    def test_switch_and_reconfiguration_discard_private_detail_and_print(self):
        session = mode.Session(self.backend)
        session.prepare("PRONOTE")
        session.prepare("Alex")
        session.receive(self.execute("grades"), "grades")
        session.prepare("ENFANT")
        session.prepare("Sam")
        self.assertIsNone(session.last)
        session.receive(self.execute("grades"), "grades")
        self.backend.configure(True, {"Sam": self.mapping["Sam"]})
        self.assertIn("Aucun resultat", session.prepare("IMPRIMER")["display"])

    def test_fallback_is_single_scoped_private_request_and_other_providers_fail_locally(self):
        request = {"fallback": "Explique mes résultats", "child": "Alex"}
        result = {"text": "Réponse", "private": True, "chunks": [], "references": []}
        with (patch.object(chat, "PROVIDER", "mistral"), patch.object(chat, "MISTRAL_KEY", "synthetic-key"),
              patch.object(chat.pronote_answers, "answer", return_value=result) as answer):
            pending = chat.start_pronote(request, self.backend, "fr", ("conversations", True, 30), "")
            pending[0].join(2)
            self.assertIsNone(pending[1].get_nowait()[1])
            self.assertIn("Alex", answer.call_args.args[0][0]["content"])
            self.assertFalse(answer.call_args.args[4])
            self.assertEqual(answer.call_count, 1)
        with patch.object(chat, "PROVIDER", "claude"), patch.object(chat.pronote_answers, "answer") as answer:
            pending = chat.start_pronote(request, self.backend, "fr", ("conversations", True, 30), "")
            pending[0].join(2)
            self.assertIn("Commande inconnue", pending[1].get_nowait()[0]["display"])
            answer.assert_not_called()

    def test_pinned_sdk_all_new_reads_allowed_all_writes_blocked(self):
        client = object.__new__(pn.client_class())
        client.communication = Mock()
        client._selected_child = Obj(id="child")
        for endpoint in ("PageBulletins", "DernieresEvaluations", "PagePresence", "PageActualites", "PageMenus"):
            client.post(endpoint, 8, {})
        for endpoint in ("SaisieActualites", "SaisieTAFFaitEleve", "SaisieReponse", "SaisieDiscussion"):
            with self.assertRaisesRegex(pn.PronoteError, "read_only"):
                client.post(endpoint, 8, {})
        self.assertEqual(client.communication.post.call_count, 5)

    def test_latest_published_report_and_weekly_print_has_full_lessons(self):
        previous = Obj(**{**self.period.__dict__, "name": "Trimestre precedent",
                          "start": self.period.start - timedelta(days=100),
                          "end": self.period.start})
        self.period.report = None
        self.client.periods = [previous, self.period]
        result = self.execute("report", period="published")
        self.assertEqual(result["period"], "Trimestre precedent")
        self.assertEqual(self.execute("report")["error"], "report_unavailable")
        week = self.execute("timetable", **{"from": self.day.isoformat(), "to": (self.day + timedelta(days=6)).isoformat()})
        self.assertIn("1 cours", mode.render(week, "timetable"))
        self.assertNotIn("Salle: A1", mode.render(week, "timetable"))
        self.assertIn("Salle: A1", mode.render(week, "timetable", full=True))

    def test_fallback_backend_rejects_another_authorized_child(self):
        scoped = mode.ScopedBackend(self.backend, "Alex")
        self.assertEqual(scoped.execute("pronote_list_children", {}), {"children": ["Alex"]})
        self.assertEqual(scoped.execute("pronote_get_grades", {"child": "Sam"}), {"error": "unknown_child"})
        for tool in scoped.tools():
            child = tool["function"]["parameters"]["properties"].get("child")
            if child:
                self.assertEqual(child["enum"], ["Alex"])
        self.factory.token_login.assert_not_called()

    def test_disabled_no_children_and_malformed_configuration(self):
        session = mode.Session(self.backend)
        self.backend.configure(False, self.mapping)
        self.assertIn("disabled", session.prepare("PRONOTE")["display"])
        self.assertFalse(session.mode)
        self.backend.configure(True, {})
        self.assertIn("aucun enfant", session.prepare("PRONOTE")["display"])
        self.assertNotIn("tool", session.prepare("COURS"))
        pn.atomic_json(self.backend.config_path, {"bad": 42})
        self.assertIn("configuration_error", session.prepare("COURS")["display"])
