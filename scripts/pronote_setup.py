#!/usr/bin/env python3
"""Local admin enrollment/diagnostics; secrets never appear in command arguments."""
import argparse
import getpass
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from services.pronote import PronoteError, backend


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    enroll = commands.add_parser("enroll")
    enroll.add_argument("account")
    enroll.add_argument("--qr-file", type=Path, required=True)
    children = commands.add_parser("children")
    children.add_argument("account")
    commands.add_parser("status")
    config = commands.add_parser("configure")
    config.add_argument("--file", type=Path, required=True)
    call = commands.add_parser("read")
    call.add_argument("kind", choices=["homework", "grades"])
    call.add_argument("child")
    call.add_argument("--from", dest="start")
    call.add_argument("--to", dest="end")
    call.add_argument("--period")
    args = parser.parse_args()
    local = backend()
    try:
        if args.command == "enroll":
            if args.qr_file.stat().st_mode & 0o077:
                raise PronoteError("qr_file_requires_mode_600")
            result = local.enroll(args.account, json.loads(args.qr_file.read_text()),
                                  getpass.getpass("QR PIN (4 digits): "),
                                  getpass.getpass("Account security PIN (or Enter): ") or None)
        elif args.command == "children":
            result = local.admin_children(args.account)
        elif args.command == "status":
            result = local.status()
        elif args.command == "configure":
            config = json.loads(args.file.read_text())
            local.configure(config["enabled"], config["children"])
            result = local.status()
        else:
            arguments = {"child": args.child}
            for key, value in (("from", args.start), ("to", args.end), ("period", args.period)):
                if value is not None:
                    arguments[key] = value
            result = local.execute("pronote_get_" + args.kind, arguments)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if isinstance(result, dict) and result.get("error") else 0
    except (PronoteError, OSError, ValueError, KeyError, TypeError) as error:
        print(error.admin_message() if isinstance(error, PronoteError) else "invalid_configuration", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
