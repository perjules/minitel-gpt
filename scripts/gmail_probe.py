#!/usr/bin/env python3
"""Manual live READ-ONLY probe. Never sends email or prints credentials/bodies."""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.gmail_auth import GmailError
from services.gmail_client import GmailClient


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--renew",
        action="store_true",
        help="force another in-memory access-token renewal",
    )
    args = parser.parse_args()
    client = GmailClient()
    try:
        deadline = time.monotonic() + 60
        records = client.list("", deadline)
        if records:
            selected = client.read(records[0]["id"], deadline)
            body_size = len(selected["body"])
        else:
            body_size = 0
        if args.renew:
            client.tokens.credentials = None
            client.tokens.token(deadline)
        print(
            json.dumps(
                {
                    "authorization": "ok",
                    "inbox_metadata_count": len(records),
                    "selected_body_characters": body_size,
                    "forced_renewal": args.renew,
                }
            )
        )
        return 0
    except GmailError as error:
        print("Gmail probe: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
