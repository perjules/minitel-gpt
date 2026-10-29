"""Offline storage, intent validation, Flask and real UART-decoder acceptance."""

import json
import threading
import unittest
from datetime import datetime
from pathlib import Path
from queue import Queue
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from test_terminal_startup import EndOfInput, FakeSerial, fn, screen_text
from web_client import csrf_client

from services import admin_ui as admin
from services import memory_session as sessions
from services import minitel_chatgpt as chat
from services import persistent_memory as memory


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = memory.MemoryStore(self.temp.name)
        self.model = Mock()
        self.alice = sessions.MemorySession("Alice", self.store, self.model)
        self.addCleanup(self.alice.close)

    def plan(self, operation, **fields):
        self.model.plan.return_value = {"operation": operation, **fields}

    def save(
        self,
        question="Retiens que je préfère les réponses courtes.",
        kind="preference",
        title="",
        text="Réponses courtes",
        session=None,
    ):
        self.plan("save", kind=kind, title=title, text=text)
        result = (session or self.alice).handle(question)
        self.assertIn("Enregistré", result["display"])
        return self.store.read((session or self.alice).key)["items"][-1]

    def test_personal_shared_separation_and_restart(self):
        self.save()
        self.alice.remember_language("en")
        bob = sessions.MemorySession("Bob", self.store, self.model)
        self.addCleanup(bob.close)
        self.assertEqual(self.store.read(bob.key)["items"], [])
        self.plan("add", kind="list", title="courses", text="lait")
        result = self.alice.handle("Ajoute du lait à la liste commune.")
        self.assertIn("mémoire commune", result["display"])
        self.assertEqual(self.store.read("shared")["items"][0]["text"], "lait")
        self.assertNotIn("lait", self.alice.context("Quelles courses ?"))
        self.assertNotIn("Réponses courtes", bob.context("Que sais-tu ?"))
        self.alice.close()
        restored = sessions.MemorySession(
            "ALICE", memory.MemoryStore(self.temp.name), self.model
        )
        self.addCleanup(restored.close)
        self.assertIn("Réponses courtes", restored.handle("MEMOIRE")["display"])
        shared = sessions.MemorySession("", self.store, self.model)
        self.addCleanup(shared.close)
        self.assertIn("lait", shared.handle("LISTE courses")["display"])

    def test_fact_correction_recall_removal_and_list_completion(self):
        record = self.save(
            "Note que les décorations sont dans le garage.",
            kind="fact",
            text="Décorations dans le garage",
        )
        self.plan("replace", ids=[record["id"]], text="Décorations au grenier")
        self.assertIn(
            "Corrigé",
            self.alice.handle("Corrige: les décorations sont au grenier.")["display"],
        )
        self.plan("show", kind="fact")
        self.assertIn(
            "grenier", self.alice.handle("Qu'est-ce que tu sais sur moi ?")["display"]
        )
        self.plan("remove", ids=[record["id"]])
        self.assertIn("Retiré", self.alice.handle("Oublie les décorations.")["display"])

    def test_add_and_complete_real_list_item(self):
        self.plan("add", kind="list", title="courses", text="piles")
        self.assertIn(
            "Ajouté",
            self.alice.handle("Ajoute des piles à ma liste de courses.")["display"],
        )
        identifier = self.store.read(self.alice.key)["items"][0]["id"]
        self.assertTrue(self.alice.interested("J'ai acheté les piles."))
        self.plan("complete", ids=[identifier])
        self.assertIn("Terminé", self.alice.handle("J'ai acheté les piles.")["display"])
        self.assertIn("[fait]", self.alice.handle("LISTE courses")["display"])
        self.assertNotIn("piles", self.alice.context("Où sont les piles ?"))

    def test_named_note_verbatim_recipe_and_duplicate_title(self):
        recipe = "Recette naan\n" + "Farine et yaourt.\n" * 250
        self.alice.record("assistant", recipe)
        self.plan(
            "save",
            kind="note",
            title="naan",
            text="Résumé incomplet",
            source="last_answer",
        )
        self.assertIn(
            recipe,
            self.alice.handle("Garde cette recette sous le nom naan.")["display"],
        )
        self.assertEqual(
            self.store.read(self.alice.key)["items"][0]["text"], recipe.strip()
        )
        self.assertIn(
            "déjà ce nom",
            self.alice.handle("Garde cette recette sous le nom naan.")["display"],
        )

    def test_context_bounded_relevant_preferences_and_no_archive_replay(self):
        self.save()
        self.save(
            "Note les outils.",
            kind="note",
            title="outils",
            text="Les outils sont au garage.",
        )
        self.save(
            "Note vacances.", kind="note", title="vacances", text="Valise rouge à Lyon."
        )
        self.alice.record("user", "Conversation seulement: licorne violette.")
        context = self.alice.context("Où sont les outils ?")
        self.assertIn("Réponses courtes", context)
        self.assertIn("garage", context)
        self.assertNotIn("Valise", context)
        self.assertNotIn("licorne", context)
        self.assertLessEqual(len(context), memory.CONTEXT_LIMIT)
        for i in range(8):
            self.save("Note texte.", kind="note", title=str(i), text="garage " * 300)
        self.assertLessEqual(len(self.alice.context("garage")), memory.CONTEXT_LIMIT)

    def test_grounded_targets_no_model_scope_and_no_unsolicited_writes(self):
        self.save()
        for proposal in (
            {"operation": "remove", "ids": ["m_0000000000000000"]},
            {"operation": "save", "kind": "fact", "text": "x", "scope": "Bob"},
            {"operation": ["save"]},
            {"operation": "save", "kind": ["fact"], "text": "x"},
            {"operation": "save", "kind": "fact"},
        ):
            self.model.plan.return_value = proposal
            self.alice.handle("Retiens quelque chose.")
            self.assertEqual(len(self.store.read(self.alice.key)["items"]), 1)
        self.plan("save", kind="fact", text="Invention")
        self.assertIn(
            "ne permet pas", self.alice.handle("Que sais-tu sur moi ?")["display"]
        )

    def test_ambiguous_shared_target_and_stale_selection(self):
        for item in ("piles AA", "piles AAA"):
            self.plan("add", kind="list", title="courses", text=item)
            self.alice.handle("Ajoute des piles à la liste commune.")
        identifiers = [r["id"] for r in self.store.read("shared")["items"]]
        self.plan("complete", ids=identifiers)
        self.assertIn(
            "Quel élément",
            self.alice.handle("J'ai acheté les piles de la liste commune.")["display"],
        )
        self.assertIn("mémoire commune", self.alice.handle("les piles AAA")["display"])
        self.assertEqual(self.store.read(self.alice.key)["items"], [])
        self.plan("remove", ids=identifiers)
        self.alice.handle("Retire les piles de la liste commune.")
        self.store.apply(
            "shared",
            {"operation": "add", "kind": "list", "title": "courses", "text": "lait"},
            {},
        )
        self.assertIn("a changé", self.alice.handle("1")["display"])
        self.assertEqual(len(self.store.read("shared")["items"]), 3)

    def test_reset_during_model_call_prevents_late_write(self):
        entered, release = threading.Event(), threading.Event()

        def delayed(*args):
            entered.set()
            release.wait(2)
            return {"operation": "save", "kind": "fact", "text": "late"}

        self.model.plan.side_effect = delayed
        worker = threading.Thread(target=lambda: self.alice.handle("Retiens ceci."))
        worker.start()
        self.assertTrue(entered.wait(1))
        self.alice.close()
        release.set()
        worker.join(1)
        self.assertFalse(worker.is_alive())
        self.assertEqual(self.store.read(self.alice.key)["items"], [])

    def test_malformed_file_preserved_and_safe_names(self):
        folder = self.store.folder(self.alice.key)
        (folder / "memory.json").write_text("broken {", encoding="utf-8")
        self.plan("save", kind="fact", text="new")
        self.assertIn("conservées", self.alice.handle("Retiens ceci.")["display"])
        self.assertEqual((folder / "memory.json").read_text(), "broken {")
        for value in ("../bob", "a/b", "a\\b", ".."):
            with self.assertRaises(memory.MemoryProblem):
                memory.user_key(value)
        self.assertEqual(memory.user_key("  Alice  ")[0], memory.user_key("ALICE")[0])
        outside = Path(self.temp.name) / "elsewhere"
        outside.mkdir()
        (Path(self.temp.name) / "users" / "evil").symlink_to(outside)
        with self.assertRaises(memory.MemoryProblem):
            self.store.read("users/evil")
        self.assertEqual(list(outside.iterdir()), [])

    def test_log_complete_dated_redacted_separate_plaintext(self):
        text = "Long échange " * 900
        with patch.dict("os.environ", {"TEST_API_KEY": "private-example-key"}):
            self.alice.record(
                "user", text + "\npassword: dangerous\nprivate-example-key"
            )
            self.alice.record("assistant", "Réponse publique")
            self.plan("save", kind="fact", text="password: dangerous")
            self.assertIn(
                "secrets", self.alice.handle("Retiens mon mot de passe.")["display"]
            )
        self.alice.close()
        logs = list(self.store.folder(self.alice.key).glob("logs/*.jsonl"))
        rows = [json.loads(line) for line in logs[0].read_text().splitlines()]
        self.assertIn(text, rows[1]["text"])
        self.assertNotIn("dangerous", logs[0].read_text())
        self.assertNotIn("private-example-key", logs[0].read_text())
        stamp = datetime.fromisoformat(rows[1]["at"])
        self.assertIsNotNone(stamp.utcoffset())
        self.assertEqual(stamp.utcoffset(), stamp.astimezone(ZoneInfo("Europe/Paris")).utcoffset())
        self.assertEqual(rows[-1]["role"], "event")
        self.assertEqual(self.store.read(self.alice.key)["items"], [])

    def test_storage_concurrent_adds_and_invalid_root(self):
        errors = []

        def add(i):
            try:
                self.store.apply(
                    self.alice.key,
                    {"operation": "add", "kind": "task", "text": str(i)},
                    {},
                )
            except (memory.MemoryProblem, OSError, ValueError) as error:
                errors.append(error)

        workers = [threading.Thread(target=add, args=(i,)) for i in range(12)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(self.store.read(self.alice.key)["items"]), 12)
        with self.assertRaises(memory.MemoryProblem):
            memory.MemoryStore(Path(__file__).resolve().parents[1] / "memory")

    def test_shared_wording_task_completion_and_secret_request(self):
        for question in (
            "Ajoute du lait à la liste de courses commune.",
            "Add milk to the shared shopping list.",
            "Note pour tout le monde : outils au garage.",
        ):
            self.assertEqual(self.alice.scope(question), "shared")
        self.plan("add", kind="task", text="Appeler le garage")
        self.assertIn(
            "Ajouté",
            self.alice.handle("Ajoute une tâche : appeler le garage.")["display"],
        )
        item = self.store.read(self.alice.key)["items"][0]
        self.plan("complete", ids=[item["id"]])
        self.assertIn(
            "Terminé", self.alice.handle("J'ai terminé la tâche garage.")["display"]
        )
        self.plan("save", kind="fact", text="value-without-label")
        self.assertIn(
            "secrets",
            self.alice.handle("Retiens mon mot de passe: value-without-label")[
                "display"
            ],
        )
        self.assertEqual(len(self.store.read(self.alice.key)["items"]), 1)

    def test_long_note_partial_correction_cannot_discard_hidden_content(self):
        item = self.save(
            "Note le texte sous le nom archive.",
            kind="note",
            title="archive",
            text="Texte complet " * 300,
        )
        self.plan("replace", ids=[item["id"]], text="Un simple extrait")
        self.assertIn(
            "extrait", self.alice.handle("Corrige la note archive.")["display"]
        )
        self.assertEqual(
            self.store.read(self.alice.key)["items"][0]["text"], item["text"]
        )
        self.assertIn(
            "Corrigé",
            self.alice.handle(
                "Remplace entièrement la note archive par un simple extrait."
            )["display"],
        )

    def test_interpreter_invalid_json_and_expired_plan(self):
        model = sessions.MemoryModel()
        with patch.object(
            model.client, "complete", return_value='{"operation":"show"}'
        ):
            self.assertEqual(
                model.plan("MEMOIRE", [], False, [], "fr", 1), {"operation": "show"}
            )
        for text in ("invalid", "[]", '{"operation":"save","path":"/tmp/x"}'):
            with (
                patch.object(model.client, "complete", return_value=text),
                self.assertRaises(memory.MemoryProblem),
            ):
                model.plan("Retiens ceci", [], False, [], "fr", 1)
        self.plan("save", kind="fact", text="late")
        self.assertIn("Délai", self.alice.handle("Retiens ceci", timeout=-1)["display"])
        self.assertEqual(self.store.read(self.alice.key)["items"], [])


class MemoryInterfaces(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.env = patch.dict(
            "os.environ", {"MINITEL_MEMORY_DIR": self.temp.name, "GMAIL_ENABLED": "0"}
        )
        self.env.start()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.env.stop)
        self.addCleanup(self.clear_admin)
        self.clear_admin()

    def clear_admin(self):
        for identifier in list(admin.MEMORY_SESSIONS):
            admin.discard_memory(identifier)

    def test_admin_selection_save_switch_reset_and_context(self):
        client = csrf_client(admin.app)
        with client.session_transaction() as cookie:
            cookie["logged_in"] = True
        request = {
            "preset_key": "p",
            "memory_user": "Alice",
            "message": "Retiens une préférence.",
        }
        with (
            patch.object(
                admin,
                "load_prompts",
                return_value={"presets": {"p": {"system": "persona"}}},
            ),
            patch.object(admin, "load_knowledge_blob", return_value=""),
            patch.object(
                sessions.MemoryModel,
                "plan",
                return_value={
                    "operation": "save",
                    "kind": "preference",
                    "text": "Réponses courtes",
                },
            ),
            patch.object(admin, "llm_answer", return_value="Réponse") as answer,
        ):
            self.assertIn(
                "Alice", client.post("/test-preset", json=request).json["answer"]
            )
            request["message"] = "Bonjour"
            client.post("/test-preset", json=request)
            self.assertIn("Réponses courtes", answer.call_args.kwargs["knowledge"])
            request.update(memory_user="Bob", message="MEMOIRE")
            self.assertIn(
                "Aucun élément",
                client.post("/test-preset", json=request).json["answer"],
            )
            request.update(memory_user="Alice", message="", reset=True)
            self.assertTrue(client.post("/test-preset", json=request).json["ok"])
            request.update(message="MEMOIRE", reset=False)
            self.assertIn(
                "Réponses courtes",
                client.post("/test-preset", json=request).json["answer"],
            )
            with client.session_transaction() as cookie:
                self.assertNotIn("Réponses courtes", str(dict(cookie)))
        client.post("/logout")
        self.assertFalse(admin.MEMORY_SESSIONS)

    def test_real_uart_username_blank_and_invalid_name(self):
        for name, expected in (("Alice", "Alice"), ("", "MinitelGPT")):
            uart = FakeSerial(name.encode() + fn(chat.K_ENVOI))
            with patch.object(chat.serial, "Serial", return_value=uart):
                term = chat.Term()
            selected, action = chat.select_memory_user(term, "fr")
            self.addCleanup(selected.close)
            self.assertEqual(selected.username, expected)
            self.assertEqual(action, "envoi")
            self.assertIn("Sans mot de passe", screen_text(uart.output))
        uart = FakeSerial(b"../x" + fn(chat.K_ENVOI) + b"Bob" + fn(chat.K_ENVOI))
        with patch.object(chat.serial, "Serial", return_value=uart):
            selected, _ = chat.select_memory_user(chat.Term(), "fr")
        self.addCleanup(selected.close)
        self.assertEqual(selected.username, "Bob")
        self.assertIn("sans chemin", screen_text(uart.output))

    def test_full_uart_startup_reset_switch_and_persistence(self):
        uart = FakeSerial(
            fn(chat.K_SOMMAIRE) + fn(chat.K_SUITE)
            + b"Alice"
            + fn(chat.K_ENVOI)
            + b"fr"
            + fn(chat.K_ENVOI)
            + b"Retiens une preference"
            + fn(chat.K_ENVOI)
            + fn(chat.K_SOMMAIRE)
            + b"LOGOUT" + fn(chat.K_ENVOI)
            + b"Bob"
            + fn(chat.K_ENVOI)
            + b"fr"
            + fn(chat.K_ENVOI)
            + b"MEMOIRE"
            + fn(chat.K_ENVOI)
            + fn(chat.K_SOMMAIRE)
        )

        def synchronous(*args, **kwargs):
            result = chat.call_answer(*args, **kwargs)
            output = Queue()
            output.put((result, None))
            return Mock(is_alive=lambda: False), output, chat.time.monotonic()

        with (
            patch.object(chat.serial, "Serial", return_value=uart),
            patch.object(chat.time, "sleep"),
            patch.object(
                chat,
                "load_preset",
                return_value=("persona", "3615 IA", "Question?", "WAIT", None),
            ),
            patch.object(chat, "build_title", return_value=["LOGO"]),
            patch.object(chat, "factual_knowledge", return_value=""),
            patch.object(
                chat, "prepare_boot", side_effect=lambda lang: chat.UI[lang]["boot"]
            ),
            patch.object(chat, "start_answer", side_effect=synchronous),
            patch.object(
                sessions.MemoryModel,
                "plan",
                return_value={
                    "operation": "save",
                    "kind": "preference",
                    "text": "Réponses courtes",
                },
            ),
            patch.object(chat.mistral_answers, "answer") as public,
            self.assertRaises(EndOfInput),
        ):
            chat.run()
        visible = screen_text(uart.output)
        self.assertIn("memoire de Alice", visible)
        self.assertIn("Aucun element dans memoire de Bob", visible)
        public.assert_not_called()
        restored = sessions.MemorySession("Alice")
        self.addCleanup(restored.close)
        self.assertIn("Réponses courtes", restored.handle("MEMOIRE")["display"])
        for path in Path(self.temp.name).glob("users/*/logs/*.jsonl"):
            self.assertNotIn("PERIPHERIQUES", path.read_text())

    def test_username_first_remembers_language_and_shared_defaults_to_french(self):
        def visit(name, language, question):
            keys = name.encode() + fn(chat.K_ENVOI)
            if language is not None:
                keys += language.encode() + fn(chat.K_ENVOI)
            return (
                keys
                + question.encode()
                + fn(chat.K_ENVOI)
                + b"LOGOUT" + fn(chat.K_ENVOI)
            )

        uart = FakeSerial(
            fn(chat.K_SOMMAIRE) + fn(chat.K_SUITE)
            + visit("Alice", "en", "Hello")
            + visit("ALICE", None, "Again")
            + visit("Bob", "fr", "Bonjour")
            + visit("", None, "Salut")
            + visit("", None, "Encore")
        )

        def synchronous(*args, **kwargs):
            result = chat.call_answer(*args, **kwargs)
            output = Queue()
            output.put((result, None))
            return Mock(is_alive=lambda: False), output, chat.time.monotonic()

        result = {"status": "ordinary", "text": "OK", "chunks": [], "references": []}
        with (
            patch.object(chat.serial, "Serial", return_value=uart),
            patch.object(chat.time, "sleep"),
            patch.object(
                chat,
                "load_preset",
                return_value=("persona", "3615 IA", "Question?", "WAIT", None),
            ),
            patch.object(chat, "build_title", return_value=["LOGO"]),
            patch.object(chat, "factual_knowledge", return_value=""),
            patch.object(
                chat, "prepare_boot", side_effect=lambda lang: chat.UI[lang]["boot"]
            ) as boot,
            patch.object(chat, "start_answer", side_effect=synchronous),
            patch.object(chat.mistral_answers, "answer", return_value=result) as answer,
            self.assertRaises(EndOfInput),
        ):
            chat.run()
        self.assertEqual(
            [call.args[0] for call in boot.call_args_list],
            ["fr"],
        )
        self.assertEqual(
            [call.args[1] for call in answer.call_args_list],
            ["en", "en", "fr", "fr", "fr"],
        )
        visible = screen_text(uart.output)
        self.assertLess(visible.index("Nom / name"), visible.index("Choose language"))
        self.assertEqual(visible.count("Choose language"), 2)
        store = memory.MemoryStore(self.temp.name)
        self.assertEqual(store.read(memory.user_key("Alice")[0])["language"], "en")
        self.assertEqual(store.read(memory.user_key("Bob")[0])["language"], "fr")
        self.assertNotIn("language", store.read("shared"))
        restored = sessions.MemorySession("Alice", memory.MemoryStore(self.temp.name))
        self.addCleanup(restored.close)
        self.assertEqual(restored.saved_language(), "en")
        for call in answer.call_args_list:
            self.assertEqual(len(call.args[0]), 1)

    def test_terminal_context_and_memory_do_not_enter_factual_history(self):
        selected = sessions.MemorySession("Alice", model=Mock())
        self.addCleanup(selected.close)
        selected.model.plan.return_value = {
            "operation": "save",
            "kind": "preference",
            "text": "Réponses courtes",
        }
        response = chat.call_answer(
            "persona",
            [{"role": "user", "content": "Retiens ma préférence."}],
            "fr",
            ("conversations", True, 60),
            "",
            memory=selected,
        )
        self.assertIsNone(response["turn"])
        result = {
            "status": "ordinary",
            "text": "Bonjour",
            "chunks": [],
            "references": [],
        }
        with patch.object(
            chat.mistral_answers, "answer", return_value=result
        ) as answer:
            chat.call_answer(
                "persona",
                [{"role": "user", "content": "Bonjour"}],
                "fr",
                ("conversations", True, 60),
                "knowledge",
                memory=selected,
            )
        self.assertIn("Réponses courtes", answer.call_args.args[6])
        self.assertNotIn("persona", answer.call_args.args[6])
