"""Metadata extraction only. Name/semantic recipient selection belongs to the LLM."""

import email.policy
import json
import re
import secrets
from datetime import datetime
from email.message import EmailMessage
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

try:
    from .gmail_auth import GmailError
    from .gmail_client import (
        addresses,
        decode_header_text,
        header_values,
        one_header,
        printable,
    )
except ImportError:
    from gmail_auth import GmailError
    from gmail_client import (
        addresses,
        decode_header_text,
        header_values,
        one_header,
        printable,
    )

CATALOGUE_TTL = 600
CATALOGUE_LIMIT = (
    64000  # RAM bound; overflows are reported, never silently shortlisted.
)
MODEL_CATALOGUE_LIMIT = 24000


def address_key(address):
    local, domain = address.rsplit("@", 1)
    return local + "@" + domain.casefold()


def catalogue(metadata, self_addresses, exact_addresses=()):
    by_address, skipped = {}, 0
    own = {a.casefold() for a in self_addresses}
    exact = {address_key(a) for a in exact_addresses}

    def timestamp(record):
        try:
            return int(record.get("internalDate", 0))
        except (ValueError, TypeError):
            return 0

    records = sorted(metadata["records"], key=timestamp, reverse=True)
    for record in records:
        try:
            try:
                moment = datetime.fromtimestamp(
                    int(record["internalDate"]) / 1000, ZoneInfo("Europe/Paris")
                )
            except (ValueError, KeyError, TypeError, OverflowError):
                moment = parsedate_to_datetime(one_header(record, "Date")).astimezone(
                    ZoneInfo("Europe/Paris")
                )
            stamp = moment.isoformat(timespec="minutes")
        except (ValueError, TypeError, GmailError):
            stamp = "Date inconnue"
        try:
            subject = printable(
                decode_header_text(one_header(record, "Subject"))
            ).replace("\n", " ")
        except GmailError:
            subject, skipped = "", skipped + 1
        # Subject examples are identification evidence, not outgoing-message facts.
        subject = subject[:120]
        direction = "envoyé" if "SENT" in record.get("labelIds", []) else "reçu"
        location = (
            " / archivé"
            if not set(record.get("labelIds", [])) & {"SENT", "INBOX"}
            else ""
        )
        for role in ("From", "Reply-To", "To", "Cc"):
            values = header_values(record, role)
            if len(values) > 1:
                skipped += 1
                continue
            for value in values:
                try:
                    if not isinstance(value, str) or len(value) > 8192:
                        raise GmailError("recipient_invalid")
                    # Validate the full header before extracting its decoded display names.
                    addresses(value)
                    parsed = EmailMessage(policy=email.policy.default)
                    parsed["To"] = re.sub(r"\r?\n[ \t]+", " ", value)
                    for address in parsed["To"].addresses:
                        actual = address.addr_spec
                        if actual.casefold() in own:
                            continue
                        key = address_key(actual)
                        if exact and key not in exact:
                            continue
                        candidate = by_address.setdefault(
                            key,
                            {
                                "id": "c_" + secrets.token_hex(8),
                                "address": actual,
                                "names": [],
                                "roles": [],
                                "last_seen": stamp,
                                "subjects": [],
                                "automated": bool(
                                    re.search(
                                        r"(?:no[._-]?reply|do[._-]?not[._-]?reply|mailer-daemon|notifications?)",
                                        actual.split("@")[0],
                                        re.IGNORECASE,
                                    )
                                ),
                            },
                        )
                        name = printable(address.display_name).strip()
                        provenance = direction + location + ": " + role
                        if name and name not in candidate["names"]:
                            candidate["names"].append(name)
                        if provenance not in candidate["roles"]:
                            candidate["roles"].append(provenance)
                        if (
                            subject
                            and subject not in candidate["subjects"]
                            and len(candidate["subjects"]) < 2
                        ):
                            candidate["subjects"].append(subject)
                except (GmailError, ValueError, AttributeError):
                    skipped += 1
        if (
            len(json.dumps(list(by_address.values()), ensure_ascii=False))
            > CATALOGUE_LIMIT
        ):
            raise GmailError("catalogue_too_large")
    return list(by_address.values()), skipped
