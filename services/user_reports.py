"""Opt-in user lifecycle reports, sent by the default profile's assigned Gmail."""

import logging
import threading
import time
from datetime import datetime
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import formatdate, make_msgid
from queue import Full, Queue
from zoneinfo import ZoneInfo

try:
    from . import gmail_auth, user_services
except ImportError:
    import gmail_auth
    import user_services

log = logging.getLogger(__name__)
PENDING = Queue(maxsize=32)
LOCK = threading.Lock()
WORKER = None


def default_sender(registry):
    if not gmail_auth.enabled():
        raise gmail_auth.GmailError("disabled")
    user = registry.get(1)
    binding = registry.gmail(1, "send")
    account = next((a for a in registry.accounts()
                    if a["id"] == binding[1] and a["revision"] == binding[2]), None)
    if account is None:
        raise gmail_auth.GmailError("account_assignment_changed")
    return user, binding, account["email"]


def configuration(registry):
    result = {"recipient": registry.report_recipient(), "ready": False, "sender": ""}
    try:
        _, _, result["sender"] = default_sender(registry)
        result["ready"] = True
    except gmail_auth.GmailError:
        pass
    return result


def enqueue(registry, action, user_id, name):
    global WORKER
    try:
        recipient = registry.report_recipient()
        if not recipient or not gmail_auth.enabled():
            return
        user, binding, sender = default_sender(registry)
        event = {"registry": registry, "action": action, "id": user_id, "name": name,
                 "recipient": recipient, "user": user, "binding": binding, "sender": sender,
                 "date": datetime.now(ZoneInfo("Europe/Paris")).isoformat(timespec="seconds")}
        with LOCK:
            PENDING.put_nowait(event)
            if WORKER is None or not WORKER.is_alive():
                WORKER = threading.Thread(target=work, name="minitel-user-reports", daemon=True)
                WORKER.start()
    except Full:
        log.warning("user_report action=%s user_id=%s status=queue_full", action, user_id)
    except gmail_auth.GmailError as error:
        log.warning("user_report action=%s user_id=%s status=skipped reason=%s",
                    action, user_id, type(error).__name__)


def work():
    while True:
        event = PENDING.get()
        try:
            send_report(event)
        except Exception as error:  # noqa: BLE001 - Keep the sender alive; never retry or log private payloads.
            log.warning("user_report action=%s user_id=%s status=failed reason=%s",
                        event["action"], event["id"], type(error).__name__)
        finally:
            PENDING.task_done()


def send_report(event):
    registry = event["registry"]
    if not gmail_auth.enabled() or registry.report_recipient() != event["recipient"]:
        return
    action = {"created": "créé", "deleted": "supprimé"}[event["action"]]
    client = user_services.ProfileMailClient(event["user"], registry)
    client.tokens.binding = event["binding"]
    client.tokens.guard("send")
    message = EmailMessage(policy=SMTP)
    message["From"], message["To"] = event["sender"], event["recipient"]
    name = " ".join(event["name"].splitlines())
    message["Subject"] = f"[3615 IA] Utilisateur {action} : {name}"
    message["Date"] = formatdate(localtime=False)
    message["Message-ID"] = make_msgid(domain=event["sender"].rsplit("@", 1)[-1])
    message["Auto-Submitted"] = "auto-generated"
    message.set_content(
        f"Rapport automatique — 3615 IA\n\nUtilisateur {action}.\n"
        f"Nom : {event['name']}\nIdentifiant : {event['id']}\n"
        f"Date : {event['date']} (Europe/Paris)\n\nEnvoyé automatiquement par MinitelGPT.\n"
    )
    with client.http:
        result = client.send(message.as_bytes(), None, str(message["Message-ID"]), time.monotonic() + 20)
    if result != "sent":
        raise gmail_auth.GmailError("send_uncertain")
    log.info("user_report action=%s user_id=%s status=sent", event["action"], event["id"])
