"""Small Gmail REST client and MIME/header parsing; no mailbox modification."""

import base64
import email.policy
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests

try:
    from .gmail_auth import GmailError, TokenSource
except ImportError:
    from gmail_auth import GmailError, TokenSource

URL = "https://gmail.googleapis.com/gmail/v1/users/me"
MAX_BODY = 65536
MAX_RESPONSE = 1048576
HEADERS = ["From", "Reply-To", "Subject", "Date", "Message-ID", "References"]
CORRESPONDENT_HEADERS = ["From", "Reply-To", "To", "Cc", "Subject", "Date"]


def printable(value):
    return "".join(
        c for c in str(value) if c in "\n\t" or (ord(c) >= 32 and ord(c) != 127)
    )


def decode_header_text(value):
    try:
        return str(make_header(decode_header(value)))
    except (ValueError, LookupError):
        return printable(value)


def header_values(data, name):
    return [
        h.get("value", "")
        for h in data.get("payload", {}).get("headers", [])
        if h.get("name", "").casefold() == name.casefold()
    ]


def one_header(data, name):
    values = header_values(data, name)
    if len(values) > 1:
        raise GmailError("ambiguous_headers")
    value = values[0] if values else ""
    if len(value) > 8192:
        raise GmailError("message_too_large")
    return value


def addresses(value):
    """Parse complete header syntax; reject defects instead of repairing addresses."""
    value = re.sub(r"\r?\n[ \t]+", " ", value)
    if not value or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise GmailError("recipient_invalid")
    try:
        msg = EmailMessage(policy=email.policy.default)
        msg["To"] = value
        header = msg["To"]
        if header.defects or not header.addresses:
            raise GmailError("recipient_invalid")
        result = []
        for item in header.addresses:
            address = item.addr_spec
            if (
                not item.username
                or not item.domain
                or not address.isascii()
                or not re.fullmatch(r"[^\s<>@]+@[^\s<>@]+", address)
            ):
                raise GmailError("recipient_invalid")
            result.append(address)
        return result
    except (ValueError, AttributeError, IndexError):
        raise GmailError("recipient_invalid") from None


def reply_addresses(data):
    reply = header_values(data, "Reply-To")
    source = reply if reply else header_values(data, "From")
    if len(source) != 1:
        raise GmailError("recipient_ambiguous" if source else "recipient_invalid")
    return addresses(source[0])


class ReadableHTML(HTMLParser):
    """No fetching, images, JS, CSS or other active content."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.output, self.hidden, self.links = [], 0, []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ("script", "style", "head"):
            self.hidden += 1
        elif not self.hidden:
            if tag in ("p", "div", "br", "li", "tr", "h1", "h2", "h3"):
                self.output.append("\n")
            elif tag in ("td", "th"):
                self.output.append(" ")
            if tag == "a":
                href = attrs.get("href", "")
                self.links.append(
                    href if href.startswith(("https://", "http://", "mailto:")) else ""
                )
            if tag == "img" and attrs.get("alt"):
                self.output.append("[" + attrs["alt"] + "]")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head") and self.hidden:
            self.hidden -= 1
        elif not self.hidden:
            if tag == "a" and self.links:
                href = self.links.pop()
                if href:
                    self.output.append(" (" + href + ")")
            if tag in ("p", "div", "li", "tr"):
                self.output.append("\n")

    def handle_data(self, value):
        if not self.hidden:
            self.output.append(value)

    def text(self):
        return re.sub(r"\n[ \t]*\n(?:[ \t]*\n)+", "\n\n", "".join(self.output)).strip()


def attachment_names(parts):
    result = []
    for part in parts:
        if part.get("filename"):
            result.append(
                {
                    "name": printable(part["filename"]),
                    "type": printable(part.get("mimeType", "unknown")),
                }
            )
        else:
            result.extend(attachment_names(part.get("parts", [])))
    return result


def contains_plain(part):
    return not part.get("filename") and (
        part.get("mimeType") == "text/plain"
        or any(contains_plain(p) for p in part.get("parts", []))
    )


def mime_content(part, attachment_loader=None):
    """Prefer plain alternatives, traverse mixed parts, never interpret attachments."""
    if part.get("filename"):
        return "", [
            {
                "name": printable(part["filename"]),
                "type": part.get("mimeType", "unknown"),
            }
        ]
    kind = part.get("mimeType", "")
    parts = part.get("parts", [])
    if parts:
        if kind == "multipart/alternative":
            preferred = next(
                (p for p in parts if contains_plain(p)),
                parts[0],
            )
            text, attachments = mime_content(preferred, attachment_loader)
            attachments += attachment_names([p for p in parts if p is not preferred])
            return text, attachments
        decoded = [mime_content(p, attachment_loader) for p in parts]
        attachments = [a for _, files in decoded for a in files]
        return "\n\n".join(text for text, _ in decoded if text), attachments
    if kind not in ("text/plain", "text/html"):
        return "", []
    body = part.get("body", {})
    if body.get("size", 0) > MAX_BODY:
        raise GmailError("message_too_large")
    encoded = body.get("data")
    if encoded is None and body.get("attachmentId"):
        if attachment_loader is None:
            raise GmailError("body_unavailable")
        encoded = attachment_loader(body["attachmentId"])
    try:
        raw = base64.urlsafe_b64decode(
            (encoded or "") + "=" * (-len(encoded or "") % 4)
        )
    except (ValueError, TypeError):
        raise GmailError("malformed_message") from None
    if len(raw) > MAX_BODY:
        raise GmailError("message_too_large")
    content_type = next(
        (
            h.get("value", "")
            for h in part.get("headers", [])
            if h.get("name", "").lower() == "content-type"
        ),
        "",
    )
    msg = EmailMessage()
    try:
        msg["Content-Type"] = content_type or kind
    except ValueError:
        raise GmailError("malformed_message") from None
    charset = msg.get_content_charset() or "utf-8"
    try:
        text = raw.decode(charset)
    except (LookupError, UnicodeError):
        text = raw.decode("utf-8", errors="replace")
        text = "[Some characters could not be decoded.]\n" + text
    if kind == "text/html":
        converter = ReadableHTML()
        converter.feed(text)
        text = converter.text()
    return printable(text), []


def message_record(data, full=False, attachment_loader=None):
    identifier = data.get("id")
    if not isinstance(identifier, str) or not re.fullmatch(
        r"[A-Za-z0-9_-]{1,128}", identifier
    ):
        raise GmailError("malformed_message")
    thread_id = data.get("threadId")
    if not isinstance(thread_id, str) or not re.fullmatch(
        r"[A-Za-z0-9_-]{1,128}", thread_id
    ):
        raise GmailError("malformed_message")
    try:
        moment = datetime.fromtimestamp(
            int(data["internalDate"]) / 1000, ZoneInfo("Europe/Paris")
        )
    except (ValueError, KeyError, TypeError, OverflowError):
        try:
            moment = parsedate_to_datetime(one_header(data, "Date")).astimezone(
                ZoneInfo("Europe/Paris")
            )
        except (ValueError, TypeError):
            moment = None
    result = {
        "id": identifier,
        "thread_id": thread_id,
        "sender": printable(decode_header_text(one_header(data, "From"))),
        "subject": printable(decode_header_text(one_header(data, "Subject"))),
        "date": moment.strftime("%d/%m/%Y %H:%M") if moment else "Date inconnue",
        "timestamp": moment.timestamp() if moment else 0,
        "unread": "UNREAD" in data.get("labelIds", []),
        "raw": data,
    }
    if full:
        result["body"], result["attachments"] = mime_content(
            data.get("payload", {}), attachment_loader
        )
        if len(result["body"].encode()) > MAX_BODY:
            raise GmailError("message_too_large")
    return result


class GmailClient:
    def __init__(self, tokens=None, http=None):
        self.tokens = tokens or TokenSource()
        self.http = http or requests.Session()

    def request(self, method, path, deadline, params=None, payload=None):
        token = self.tokens.token(deadline)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise GmailError("timeout")
        try:
            with self.http.request(
                method,
                URL + path,
                params=params,
                json=payload,
                headers={"Authorization": "Bearer " + token},
                timeout=min(15, remaining),
                allow_redirects=False,
                stream=True,
            ) as response:
                if response.status_code == 401:
                    self.tokens.credentials = None
                    raise GmailError("reconnect_required")
                if response.status_code >= 400:
                    raise GmailError(
                        "permission_denied"
                        if response.status_code == 403
                        else "unavailable"
                    )
                if not 200 <= response.status_code < 300:
                    raise GmailError("unavailable")
                chunks, size = [], 0
                for chunk in response.iter_content(8192):
                    size += len(chunk)
                    if size > MAX_RESPONSE:
                        raise GmailError("message_too_large")
                    if time.monotonic() >= deadline:
                        raise GmailError("timeout")
                    chunks.append(chunk)
                import json

                result = json.loads(b"".join(chunks))
                if not isinstance(result, dict):
                    raise GmailError("malformed_response")
                return result
        except requests.RequestException:
            raise GmailError("unavailable") from None
        except (ValueError, TypeError):
            raise GmailError("malformed_response") from None

    def list(self, query, deadline):
        if (
            not isinstance(query, str)
            or len(query) > 256
            or any(ord(c) < 32 for c in query)
        ):
            raise GmailError("invalid_search")

        def paris_boundary(match):
            try:
                moment = datetime.strptime(
                    match[2].replace("/", "-"), "%Y-%m-%d"
                ).replace(tzinfo=ZoneInfo("Europe/Paris"))
                return match[1] + ":" + str(int(moment.timestamp()))
            except ValueError:
                raise GmailError("invalid_search") from None

        query = re.sub(
            r"\b(after|before):([0-9]{4}[-/][0-9]{2}[-/][0-9]{2})\b",
            paris_boundary,
            query,
        )
        params = {"maxResults": 10, "includeSpamTrash": "false"}
        if query:
            params["q"] = query
        else:
            params["labelIds"] = "INBOX"
        data = self.request("GET", "/messages", deadline, params=params)
        entries = data.get("messages", [])
        if not isinstance(entries, list) or len(entries) > 10:
            raise GmailError("malformed_response")
        records, seen = [], set()
        for entry in entries:
            if not isinstance(entry, dict):
                raise GmailError("malformed_response")
            identifier = entry.get("id")
            if not isinstance(identifier, str):
                raise GmailError("malformed_response")
            if identifier in seen:
                continue
            seen.add(identifier)
            records.append(self.read(identifier, deadline, full=False))
        return sorted(records, key=lambda r: r["timestamp"], reverse=True)

    def read(self, identifier, deadline, full=True):
        if not isinstance(identifier, str) or not re.fullmatch(
            r"[A-Za-z0-9_-]{1,128}", identifier
        ):
            raise GmailError("invalid_selection")
        params = {"format": "full" if full else "metadata"}
        if not full:
            params["metadataHeaders"] = HEADERS
        data = self.request(
            "GET", "/messages/" + quote(identifier, safe=""), deadline, params=params
        )
        if data.get("id") != identifier:
            raise GmailError("message_identity_mismatch")
        return message_record(
            data,
            full,
            lambda attachment: self.request(
                "GET",
                "/messages/"
                + identifier
                + "/attachments/"
                + quote(attachment, safe=""),
                deadline,
            )["data"],
        )

    def send(self, raw, thread_id, message_id, deadline):
        """Exactly one send attempt. Caller retires uncertain attempts permanently."""
        try:
            payload = {"raw": base64.urlsafe_b64encode(raw).decode()}
            if thread_id is not None:
                payload["threadId"] = thread_id
            result = self.request(
                "POST",
                "/messages/send",
                deadline,
                payload=payload,
            )
            if not isinstance(result.get("id"), str) or not re.fullmatch(
                r"[A-Za-z0-9_-]{1,128}", result["id"]
            ):
                raise GmailError("send_uncertain")
            return "sent"
        except (GmailError, requests.RequestException) as error:
            if str(error) in (
                "reconnect_required",
                "credential_helper_unavailable",
                "dependency_missing",
            ):
                raise
            # No send retry. A bounded read-only reconciliation may prove success.
            try:
                found = self.request(
                    "GET",
                    "/messages",
                    deadline,
                    params={"q": "in:sent rfc822msgid:" + message_id, "maxResults": 1},
                )
                candidates = found.get("messages", [])
                if (
                    isinstance(candidates, list)
                    and len(candidates) == 1
                    and isinstance(candidates[0], dict)
                ):
                    identifier = candidates[0].get("id")
                    if isinstance(identifier, str) and re.fullmatch(
                        r"[A-Za-z0-9_-]{1,128}", identifier
                    ):
                        verified = self.read(identifier, deadline, full=False)
                        if (
                            one_header(verified["raw"], "Message-ID").strip()
                            == message_id
                        ):
                            return "sent"
            except GmailError:
                pass
            raise GmailError("send_uncertain") from None

    def correspondent_metadata(self, deadline):
        """Up to 50 recent non-draft messages, metadata only. Five connection pools.

        Each worker owns its HTTP Session; only the locked token source is shared.
        Results never enter the ordinary ten-message inbox list or body cache.
        """
        identifiers, page, partial = [], None, False
        for _ in range(5):
            params = {
                "maxResults": 50 - len(identifiers),
                "includeSpamTrash": "false",
                "q": "-in:drafts -in:spam -in:trash",
                "fields": "messages(id),nextPageToken",
            }
            if page:
                params["pageToken"] = page
            data = self.request("GET", "/messages", deadline, params=params)
            entries = data.get("messages", [])
            if not isinstance(entries, list) or len(entries) > params["maxResults"]:
                raise GmailError("malformed_response")
            for entry in entries:
                identifier = entry.get("id") if isinstance(entry, dict) else None
                if not isinstance(identifier, str) or not re.fullmatch(
                    r"[A-Za-z0-9_-]{1,128}", identifier
                ):
                    raise GmailError("malformed_response")
                if identifier not in identifiers:
                    identifiers.append(identifier)
            next_page = data.get("nextPageToken")
            if not next_page or len(identifiers) == 50:
                break
            if (
                not isinstance(next_page, str)
                or len(next_page) > 2048
                or next_page == page
            ):
                raise GmailError("malformed_response")
            page = next_page
        else:
            partial = True

        def fetch(group):
            records, failures = [], 0
            with requests.Session() as http:
                client = GmailClient(self.tokens, http)
                for identifier in group:
                    try:
                        data = client.request(
                            "GET",
                            "/messages/" + identifier,
                            deadline,
                            params={
                                "format": "metadata",
                                "metadataHeaders": CORRESPONDENT_HEADERS,
                                "fields": "id,internalDate,labelIds,payload(headers)",
                            },
                        )
                        if data.get("id") != identifier:
                            raise GmailError("message_identity_mismatch")
                        headers = data.get("payload", {}).get("headers", [])
                        labels = data.get("labelIds", [])
                        if (
                            not isinstance(headers, list)
                            or len(headers) > 50
                            or any(
                                not isinstance(h, dict)
                                or not isinstance(h.get("name"), str)
                                or not isinstance(h.get("value"), str)
                                for h in headers
                            )
                            or sum(len(h["name"]) + len(h["value"]) for h in headers)
                            > 32768
                            or not isinstance(labels, list)
                            or any(not isinstance(v, str) for v in labels)
                        ):
                            raise GmailError("malformed_response")
                        # Retain only the contract fields, even if an API fixture/server
                        # supplies extra fields despite the partial-response selector.
                        data = {
                            "id": identifier,
                            "internalDate": data.get("internalDate"),
                            "labelIds": labels,
                            "payload": {"headers": headers},
                        }
                        if not set(data.get("labelIds", [])) & {
                            "DRAFT",
                            "SPAM",
                            "TRASH",
                        }:
                            records.append(data)
                    except GmailError as error:
                        if str(error) in {
                            "reconnect_required",
                            "permission_denied",
                            "credential_helper_unavailable",
                        }:
                            raise
                        failures += 1
            return records, failures

        records, failures = [], 0
        with ThreadPoolExecutor(max_workers=5) as pool:
            jobs = [
                pool.submit(fetch, identifiers[i::5])
                for i in range(min(5, len(identifiers)))
            ]
            for job in jobs:
                group, failed = job.result()
                records.extend(group)
                failures += failed
        return {
            "records": records,
            "attempted": len(identifiers),
            "failed": failures,
            "partial": partial or bool(failures),
        }
