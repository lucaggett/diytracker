"""Promoter notification emails.

Sent through services/mail.py, and like the invite emails these
deliberately bypass the Babel catalogs (they are sent, not rendered in a
request, so there is no locale to bind — German is the site's base
language). Callers must invoke notify_* AFTER their commit and treat
failures as non-fatal: an SMTP outage must never roll back or 500 the
action that triggered the mail.
"""

from diytracker.services.mail import build_logger, send_mail


def build_notification_logger():
    return build_logger("notifications", "notifications.log")


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
        send_mail(owner.email, subject, body)
        logger.info("label-edit mail to %s for event %s", owner.email, event.id)
        return True
    except Exception:
        logger.exception(
            "label-edit mail to %s for event %s failed", owner.email, event.id
        )
        return False
