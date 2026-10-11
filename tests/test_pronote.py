"""Read-only parent boundaries, token durability, isolation and cached outages."""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace as Obj
from unittest.mock import Mock, patch

import requests

from services import pronote as pn


class PronoteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.factory = Mock()
        self.local = pn.Pronote(self.root, lambda: self.factory)
        self.local.configure(True, {"Alex": {"account": "famille", "name": "NOM Alex"},
                                   "Sam": {"account": "famille", "name": "NOM Sam"}})
        self.creds = {"pronote_url": "https://school.example/pronote/mobile.parent.html",
                      "username": "PRIVATE USER", "password": "PRIVATE TOKEN", "uuid": "fixed-device",
                      "client_identifier": "PRIVATE ID"}
        pn.atomic_json(self.local.credentials_path("famille"), self.creds)
        self.clients = []
        self.factory.token_login.side_effect = self.client
        self.day = pn.today()

    def tearDown(self):
        self.temp.cleanup()

    def client(self, **credentials):
        obj = Mock()
        obj.logged_in = True
        obj.children = [Obj(name="NOM Alex", id="volatile-1"), Obj(name="NOM Sam", id="volatile-2")]
        obj.export_credentials.return_value = {**self.creds, "password": f"NEW TOKEN {len(self.clients)}"}
        obj.homework.return_value = [Obj(date=self.day, subject=Obj(name="Maths"),
                                        description="<b>Exercice 2</b> https://secret.example/a\x00",
                                        done=False, files="PRIVATE ATTACHMENT URL")]
        period = Obj(name="Trimestre 1", start=datetime.combine(self.day - timedelta(days=10), datetime.min.time()),
                     end=datetime.combine(self.day + timedelta(days=30), datetime.min.time()),
                     grades=[Obj(date=self.day, subject=Obj(name="Maths"), grade="Absent", out_of="20",
                                 coefficient="0,5", comment="Contrôle", average="PRIVATE CLASS AVERAGE")])
        obj.periods = [period]
        obj.current_period = period
        self.clients.append(obj)
        return obj

    def read(self, child="Alex", **args):
        return self.local.execute("pronote_get_homework", {"child": child, **args})

    def test_allowlist_and_arguments_fail_before_login(self):
        for name, args in [("pronote_enroll", {"pin": "1234"}),
                           ("pronote_get_homework", {"child": "Other"}),
                           ("pronote_get_homework", {"child": "Alex", "done": True}),
                           ("pronote_get_homework", {"child": "Alex", "from": "2026-10-12", "to": "2026-10-11"}),
                           ("pronote_get_homework", {"child": "Alex", "from": "2026-01-01", "to": "2026-03-01"}),
                           ("pronote_get_homework", '{bad'), ("pronote_get_homework", [])]:
            self.assertIn("error", self.local.execute(name, args))
        self.factory.token_login.assert_not_called()
        tools = json.dumps(self.local.tools())
        self.assertNotIn("pronote_enroll", tools)
        for secret in ["NOM Alex", "NOM Sam", "PRIVATE", "school.example"]:
            self.assertNotIn(secret, tools)
        self.assertEqual(self.local.execute("pronote_list_children", {}), {"children": ["Alex", "Sam"]})

    def test_fresh_client_correct_child_token_rotation_and_permissions(self):
        first = self.read()
        second = self.read("Sam")
        self.assertNotIn("error", first)
        self.clients[0].set_child.assert_called_once_with(self.clients[0].children[0])
        self.clients[1].set_child.assert_called_once_with(self.clients[1].children[1])
        self.assertEqual(self.factory.token_login.call_args_list[1].kwargs["password"], "NEW TOKEN 0")
        self.assertEqual(pn.read_json(self.local.credentials_path("famille"))["password"], "NEW TOKEN 1")
        self.assertEqual(first["items"][0]["description"], "Exercice 2 [link omitted]")
        self.assertNotIn("PRIVATE", json.dumps([first, second, self.local.status()]))
        self.assertEqual(os.stat(self.local.directory).st_mode & 0o777, 0o700)
        self.assertEqual(os.stat(self.local.credentials_path("famille")).st_mode & 0o777, 0o600)
        for obj in self.clients:
            obj.communication.session.close.assert_called_once()
            obj.refresh.assert_not_called()

    def test_cached_outage_preserves_time_and_never_crosses_child_or_range(self):
        first = self.read()
        cached = self.read()
        self.assertTrue(cached["cached"])
        self.factory.token_login.assert_called_once()
        key = next(iter(self.local.cache))
        stamp, value = self.local.cache[key]
        self.local.cache[key] = (stamp - pn.CACHE_SECONDS - 1, value)
        self.factory.token_login.side_effect = requests.ConnectionError("SECRET BAD URL")
        stale = self.read()
        self.assertTrue(stale["stale"])
        self.assertEqual(first["retrieved_at"], stale["retrieved_at"])
        self.assertEqual(self.read("Sam"), {"error": "unavailable"})
        self.assertEqual(self.read(**{"from": "2026-10-12", "to": "2026-10-12"}), {"error": "unavailable"})
        self.factory.token_login.side_effect = pn.PronoteError("authentication_required")
        self.assertEqual(self.read(), {"error": "authentication_required"})
        self.local.cache[key] = (stamp - pn.STALE_SECONDS - 1, value)
        self.factory.token_login.side_effect = requests.ConnectionError()
        self.assertEqual(self.read(), {"error": "unavailable"})

    def test_grade_strings_and_period_selection_without_computed_averages(self):
        result = self.local.execute("pronote_get_grades", {"child": "Alex"})
        self.assertEqual(result["period"], "Trimestre 1")
        self.assertEqual(result["items"][0]["grade"], "Absent")
        self.assertEqual(result["items"][0]["coefficient"], "0,5")
        self.assertNotIn("average", json.dumps(result))
        unknown = self.local.execute("pronote_get_grades", {"child": "Alex", "period": "Unknown"})
        self.assertEqual(unknown, {"error": "select_period", "periods": ["Trimestre 1"]})

    def test_cache_invalidated_by_mapping_change_and_missing_or_duplicate_child(self):
        self.read()
        self.local.configure(True, {"Alex": {"account": "famille", "name": "MISSING"}})
        self.assertEqual(self.read(), {"error": "child_unavailable"})
        self.assertEqual(len(self.clients), 2)
        self.assertEqual(pn.read_json(self.local.credentials_path("famille"))["password"], "NEW TOKEN 1")
        self.local.configure(False, {})
        self.assertEqual(self.read(), {"error": "disabled"})
        self.assertEqual(self.local.tools(), [])

    def test_enroll_saves_qr_token_then_rotated_token_and_keeps_allowlist(self):
        qr = {"url": "https://school.example/pronote/parent.html", "login": "aabb", "jeton": "ccdd"}
        self.factory.qrcode_login.return_value = self.client()
        names = self.local.enroll("new-account", qr, "1234")
        self.assertEqual(names, ["NOM Alex", "NOM Sam"])
        self.assertEqual(self.factory.token_login.call_args.kwargs["password"], "NEW TOKEN 0")
        credentials = pn.read_json(self.local.credentials_path("new-account"))
        self.assertEqual(credentials["password"], "NEW TOKEN 1")
        self.assertNotIn("pin", credentials)
        self.assertNotIn("jeton", credentials)
        self.assertEqual(len(self.local.config()["children"]), 2)
        for url in ("http://school/parent.html", "https://school/eleve.html"):
            with self.assertRaises(pn.PronoteError):
                self.local.enroll("new-account", {**qr, "url": url}, "1234")
        with self.assertRaises(pn.PronoteError):
            self.local.enroll("../escape", qr, "1234")

    def test_failed_second_enrollment_login_keeps_first_token(self):
        qr = {"url": "https://school.example/parent.html", "login": "aa", "jeton": "bb"}
        self.factory.qrcode_login.return_value = self.client()
        self.factory.token_login.side_effect = RuntimeError("SECRET SDK error")
        with self.assertRaisesRegex(pn.PronoteError, "enrollment_failed"):
            self.local.enroll("new-account", qr, "1234")
        self.assertEqual(pn.read_json(self.local.credentials_path("new-account"))["password"], "NEW TOKEN 0")

    def test_enrollment_diagnostics_distinguish_qr_and_token_failures_without_secrets(self):
        from pronotepy.exceptions import MFAError, QRCodeDecryptError

        qr = {"url": "https://school.example/parent.html", "login": "AB" * 16, "jeton": "CD" * 32}
        self.factory.qrcode_login.side_effect = QRCodeDecryptError("SECRET QR PIN AND TOKEN")
        with self.assertLogs(pn.log, level="WARNING") as logs, self.assertRaises(pn.PronoteError) as caught:
            self.local.enroll("famille", qr, "1234")
        self.assertEqual(caught.exception.diagnostic, {"stage": "qr_login", "error": "QRCodeDecryptError"})
        self.assertNotIn("SECRET", str(logs.output) + caught.exception.admin_message())
        self.assertNotIn(qr["jeton"], str(logs.output))
        self.factory.qrcode_login.side_effect = None
        self.factory.qrcode_login.return_value = self.client()
        self.factory.token_login.side_effect = MFAError("SECRET ACCOUNT PIN", pronote_error_code=25,
                                                       pronote_error_msg="SECRET SERVER BODY")
        with self.assertLogs(pn.log, level="WARNING") as logs, self.assertRaises(pn.PronoteError) as caught:
            self.local.enroll("famille", qr, "1234")
        self.assertEqual(caught.exception.diagnostic, {"stage": "token_login", "error": "MFAError", "code": 25})
        self.assertEqual(pn.read_json(self.local.credentials_path("famille"))["password"], "NEW TOKEN 0")
        self.assertNotIn("SECRET", str(logs.output) + caught.exception.admin_message())

    def test_credential_save_failure_is_distinct_from_authentication_failure(self):
        qr = {"url": "https://school.example/parent.html", "login": "AB" * 16, "jeton": "CD" * 32}
        self.factory.qrcode_login.return_value = self.client()
        with (patch.object(pn, "atomic_json", side_effect=PermissionError("SECRET ACCOUNT PATH")),
              self.assertLogs(pn.log, level="WARNING") as logs,
              self.assertRaises(pn.PronoteError) as caught):
            self.local.enroll("famille", qr, "1234")
        self.assertEqual(str(caught.exception), "credential_storage_failed")
        self.assertEqual(caught.exception.diagnostic, {"stage": "credential_save", "error": "PermissionError"})
        self.assertNotIn("SECRET", str(logs.output) + caught.exception.admin_message())
        self.factory.token_login.assert_not_called()

    def test_read_tool_errors_exclude_admin_diagnostic_details(self):
        self.factory.token_login.side_effect = pn.PronoteError("authentication_required")
        with self.assertLogs(pn.log, level="WARNING"):
            result = self.read()
        self.assertEqual(result, {"error": "authentication_required"})

    def test_real_mobile_qr_metadata_is_ignored_without_changing_connection_fields(self):
        # Synthetic credentials only; never retain a real QR payload in fixtures.
        core = {"url": "https://school.example/pronote/mobile.parent.html",
                "login": "AB" * 16, "jeton": "CD" * 32}
        qr = {"avecPageConnexion": False, **core, "futureMobileOption": {"enabled": True}}
        snapshot = json.loads(json.dumps(qr))
        self.factory.qrcode_login.return_value = self.client()
        self.local.enroll(" Famille ", qr, "1234")
        self.assertEqual(self.factory.qrcode_login.call_args.args[0], core)
        self.assertEqual(qr, snapshot)
        self.assertEqual(self.local.credentials_path(" FAMILLE "), self.local.credentials_path("famille"))
        self.assertNotIn("avecPageConnexion", json.dumps(pn.read_json(self.local.credentials_path("famille"))))

    def test_mobile_account_capitalization_is_canonical_in_configuration(self):
        children = {"Alex": {"account": " Famille ", "name": "NOM Alex"}}
        self.local.configure(True, children)
        self.assertEqual(children["Alex"]["account"], " Famille ")
        self.assertEqual(self.local.config()["children"]["Alex"]["account"], "famille")
        self.assertNotIn("error", self.read())
        pn.atomic_json(self.local.config_path, {"enabled": True, "children": children})
        self.assertEqual(self.local.config()["children"]["Alex"]["account"], "famille")
        for unsafe in ("../famille", "famille/other", "", "name with spaces"):
            with self.assertRaises(pn.PronoteError):
                pn.slug(unsafe)

    def test_missing_empty_or_malformed_required_qr_fields_reject_before_login(self):
        core = {"url": "https://school.example/parent.html", "login": "AB" * 16, "jeton": "CD" * 32}
        for field in core:
            for bad in (None, False, "", {}):
                with self.assertRaisesRegex(pn.PronoteError, "invalid_qr"):
                    self.local.enroll("famille", {**core, field: bad, "avecPageConnexion": False}, "1234")
            missing = {key: value for key, value in core.items() if key != field}
            with self.assertRaisesRegex(pn.PronoteError, "invalid_qr"):
                self.local.enroll("famille", missing, "1234")
        self.factory.qrcode_login.assert_not_called()

    def test_lock_busy_and_atomic_save_failure_preserve_previous_token(self):
        with self.local.lock("famille", time.monotonic() + 1):
            other = pn.Pronote(self.root, lambda: self.factory)
            self.assertEqual(other.execute("pronote_get_homework", {"child": "Alex"},
                                          deadline=time.monotonic() + .02), {"error": "busy"})
        path = self.local.credentials_path("famille")
        with patch.object(pn.os, "replace", side_effect=OSError("full")), self.assertRaises(OSError):
            pn.atomic_json(path, {"password": "NEW"})
        self.assertEqual(pn.read_json(path), self.creds)
        self.assertFalse(list(self.local.directory.glob(".tmp-*")))


    def test_external_process_lock_blocks_login(self):
        lock_path = self.local.directory / "famille.lock"
        script = "import fcntl,sys; f=open(sys.argv[1],'w'); fcntl.flock(f,fcntl.LOCK_EX); print('locked',flush=True); sys.stdin.read()"
        process = subprocess.Popen([sys.executable, "-c", script, str(lock_path)],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(), "locked")
            result = self.local.execute("pronote_get_homework", {"child": "Alex"},
                                        deadline=time.monotonic() + .02)
            self.assertEqual(result, {"error": "busy"})
            self.factory.token_login.assert_not_called()
        finally:
            process.communicate(timeout=2)
        self.assertNotIn("error", self.read())

    def test_reenrollment_uuid_invalidates_other_process_cache_and_bounded_results(self):
        self.read()
        pn.atomic_json(self.local.credentials_path("famille"), {**self.creds, "uuid": "new-enrollment"})
        self.assertFalse(self.read()["cached"])
        self.assertEqual(len(self.clients), 2)
        self.local.cache.clear()
        obj = self.client()
        obj.homework.return_value *= 101
        self.factory.token_login.side_effect = None
        self.factory.token_login.return_value = obj
        result = self.read()
        self.assertTrue(result["truncated"])
        self.assertLessEqual(len(result["items"]), pn.MAX_ITEMS)
        obj.homework.return_value = [Obj(date=self.day, subject=Obj(name="Maths"), description="a" * 5000, done=False)]
        self.local.cache.clear()
        result = self.read()
        self.assertTrue(result["truncated"])
        self.assertEqual(len(result["items"][0]["description"]), 2000)


class PinnedSDKTests(unittest.TestCase):
    def test_pronote_2026_identification_encrypts_raw_challenge(self):
        # Exercise the real pinned SDK login through our read-only hook.
        # A newer server returns a raw challenge, not ciphertext to decrypt.
        from Crypto.Hash import SHA256
        from pronotepy.pronoteAPI import _Encryption

        client = object.__new__(pn.client_class())
        client.ent = None
        client.username = "synthetic-user"
        client.password = "synthetic-token"
        client.attributes = {"a": "3"}
        client.login_mode = "qr_code"
        client.uuid = "synthetic-device"
        client.communication = Mock()
        client.communication.encryption.aes_iv = bytes(16)
        challenge = "00" * 16
        response = {"dataSec": {"data": {"challenge": challenge,
                    "modeCompLog": False, "modeCompMdp": False}}}
        client.communication.post.side_effect = [response, RuntimeError("stop after challenge")]

        with (patch("pronotepy.clients._Encryption.aes_decrypt",
                    side_effect=AssertionError("raw challenge must not be decrypted")),
              self.assertRaisesRegex(RuntimeError, "stop after challenge")):
            client._login()

        calls = client.communication.post.call_args_list
        self.assertEqual([call.args[0] for call in calls], ["Identification", "Authentification"])
        encryption = _Encryption()
        encryption.aes_set_iv(bytes(16))
        digest = SHA256.new(client.password.encode()).hexdigest().upper()
        encryption.aes_set_key((client.username + digest).encode())
        self.assertEqual(calls[1].args[1]["data"]["challenge"],
                         encryption.aes_encrypt(challenge.encode()).hex())

    def test_readonly_post_and_early_save_even_if_user_parameters_fail(self):
        cls = pn.client_class()
        client = object.__new__(cls)
        client.communication = Mock()
        client.communication.post.side_effect = RuntimeError("malformed user response")
        client.export_credentials = Mock(return_value={"password": "ROTATED"})
        save = Mock()
        token = pn.SAVE.set(save)
        try:
            with self.assertRaises(RuntimeError):
                client.post("ParametresUtilisateur")
            save.assert_called_once_with({"password": "ROTATED"})
            with self.assertRaisesRegex(pn.PronoteError, "read_only"):
                client.post("SaisieTAFFaitEleve", 88, {"TAFFait": True})
        finally:
            pn.SAVE.reset(token)
        self.assertEqual(client.communication.post.call_count, 1)

    def test_sdk_transport_timeout_and_expired_deadline(self):
        pn.client_class()
        from pronotepy.clients import _Communication
        from requests.adapters import HTTPAdapter
        obj = _Communication("https://school.example/pronote/parent.html", None, True)
        adapter = obj.session.get_adapter("https://school.example")
        token = pn.DEADLINE.set(time.monotonic() + 4)
        try:
            with patch.object(HTTPAdapter, "send", return_value=Mock()) as send:
                adapter.send(Mock(), timeout=None)
                self.assertTrue(0 < send.call_args.kwargs["timeout"] <= 4)
            pn.DEADLINE.set(time.monotonic() - 1)
            with self.assertRaisesRegex(pn.PronoteError, "timeout"):
                adapter.send(Mock())
        finally:
            pn.DEADLINE.reset(token)
            obj.session.close()
