"""Promoter notification emails.

Same SMTP pattern as admin/invites.py, and like the invite emails these
deliberately bypass the Babel catalogs (they are sent, not rendered in a
request, so there is no locale to bind — German is the site's base
language). Callers must invoke notify_* AFTER their commit and treat
failures as non-fatal: an SMTP outage must never roll back or 500 the
action that triggered the mail.
"""

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage

from diytracker.paths import LOGS_DIR


def build_notification_logger():
    logger = logging.getLogger("diytracker.notifications")
    if not logger.handlers:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(LOGS_DIR / "notifications.log")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger


def _send_email(to_address, subject, body):
    message = EmailMessage()
    message.set_content(body)
    message["Subject"] = subject
    message["From"] = "info@diytracker.ch"
    message["To"] = to_address

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(os.environ["EMAIL_SERVER"], 465, context=context) as server:
        server.login(os.environ["EMAIL_USERNAME"], os.environ["EMAIL_PASSWORD"])
        server.send_message(message)


def notify_label_event_edited(owner, event, label):
    """Email *owner* that an admin touched an event carrying their label.

    Returns True when the mail went out; swallows and logs everything else.
    """
    logger = build_notification_logger()
    if not owner.notify_label_events:
        return False
    subject = f"diytracker: «{event.name}» wurde bearbeitet"
    body = (
        f"Hallo,\n\n"
        f"ein Admin hat das Event «{event.name}» "
        f"({event.date:%d.%m.%Y}) bearbeitet, das dein Label "
        f"«{label.name}» trägt.\n\n"
        f"Aktueller Stand: https://diytracker.ch/events/{event.id}/\n"
        f"Dein Dashboard: https://diytracker.ch/promoter/\n\n"
        f"Du bekommst diese Mail, weil Benachrichtigungen für dein Konto "
        f"aktiviert sind. Abschalten kannst du sie im Promoter-Dashboard.\n"
    )
    try:
        _send_email(owner.email, subject, body)
        logger.info("label-edit mail to %s for event %s", owner.email, event.id)
        return True
    except Exception:
        logger.exception(
            "label-edit mail to %s for event %s failed", owner.email, event.id
        )
        return False
