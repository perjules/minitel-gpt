"""Credential-enabled console launch without touching Gmail or systemd."""

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import terminal_console as console


class GmailLauncherTests(unittest.TestCase):
    def test_launch_uses_shared_venv_and_delivers_credentials_without_recursion(self):
        with (
            patch.object(console, "__file__", "/tmp/checkout with spaces/scripts/terminal_console.py"),
            patch.object(Path, "is_file", return_value=True),
            patch.dict(os.environ, {
                "TERM": "xterm-256color", "MINITEL_MEMORY_DIR": "/tmp/test memory",
                "UNRELATED_SECRET": "must-not-be-forwarded",
            }, clear=True),
            patch.object(console.subprocess, "call", return_value=7) as run,
        ):
            self.assertEqual(console.launch_gmail_console(), 7)
        command = run.call_args.args[0]
        self.assertEqual(command[:2], ["sudo", "systemd-run"])
        self.assertIn("--pty", command)
        self.assertIn("--uid=minitel", command)
        self.assertIn("--property=LoadCredentialEncrypted=gmail:/etc/credstore.encrypted/minitel-gmail", command)
        self.assertIn("--setenv=GMAIL_ENABLED=1", command)
        self.assertIn("--setenv=TERM=xterm-256color", command)
        self.assertIn("--setenv=MINITEL_MEMORY_DIR=/tmp/test memory", command)
        self.assertEqual(command[-2:], [
            "/opt/minitel-gpt/venv/bin/python",
            "/tmp/checkout with spaces/scripts/terminal_console.py",
        ])
        self.assertNotIn("--gmail", command)
        self.assertNotIn("must-not-be-forwarded", " ".join(command))
        self.assertEqual(run.call_args.kwargs, {})

    def test_development_console_falls_back_to_checkout_environment(self):
        with (
            patch.object(console, "__file__", "/tmp/checkout/scripts/terminal_console.py"),
            patch.object(Path, "is_file", lambda path: str(path).startswith("/tmp/checkout/.venv")),
            patch.object(console.subprocess, "call", return_value=0) as run,
        ):
            assert console.launch_gmail_console() == 0
        assert run.call_args.args[0][-2] == "/tmp/checkout/.venv/bin/python"

    def test_missing_launcher_returns_actionable_error(self):
        with (
            patch.object(console.subprocess, "call", side_effect=FileNotFoundError),
            patch("sys.stderr") as stderr,
        ):
            self.assertEqual(console.launch_gmail_console(), 2)
        self.assertIn("sudo and systemd-run", stderr.write.call_args_list[0].args[0])

    def test_flag_launches_before_importing_application(self):
        with (
            patch("sys.argv", ["terminal_console.py", "--gmail"]),
            patch("sys.stdin.isatty", return_value=True),
            patch("sys.stdout.isatty", return_value=True),
            patch.dict(os.environ, {}, clear=True),
            patch.object(console, "launch_gmail_console", return_value=0) as launch,
        ):
            self.assertEqual(console.main(), 0)
        launch.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
