"""One SMTP path and one log-file convention for every mail the app sends.

Three call sites used to carry their own copy of this: the contact form, the
promoter notifications and the invite emails each opened their own
``SMTP_SSL`` on ``EMAIL_SERVER``, logged in with ``os.environ`` lookups and
built their own ``FileHandler``. Only the message bodies actually differed,
and those stay with their callers — the German/Schwiizerdütsch texts belong
next to the logic that decides when to send them, not here.

Mail is deliberately *not* localized through flask-babel: these are sent, not
rendered in a request, so there is no locale to bind (see admin/invites.py).

Credentials come from the environment rather than ``Config`` because mail is
also sent from the admin CLI, which builds its own app; ``send_mail`` raises
``MailNotConfiguredError`` when they are missing so a caller can tell "not set up"
apart from "the server rejected it".
"""

import logging
import smtplib
import ssl
from email.message import EmailMessage

from diytracker.config import email_settings
from diytracker.paths import LOGS_DIR

FROM_ADDRESS = "info@diytracker.ch"


class MailNotConfiguredError(RuntimeError):
    """EMAIL_SERVER / EMAIL_USERNAME / EMAIL_PASSWORD are not all set."""


def build_logger(name, filename):
    """A module-private file logger under logs/, created once per name.

    Mail is fire-and-forget for most callers, so the record of what went out
    (and what failed) is the only thing left to debug from.
    """
    logger = logging.getLogger(f"diytracker.{name}")
    if not logger.handlers:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(LOGS_DIR / filename)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger


def send_mail(to_address, subject, body, reply_to=None):
    """Send one plain-text mail over implicit TLS. Raises on failure."""
    host, username, password = email_settings()
    if not (host and username and password):
        raise MailNotConfiguredError(
            "EMAIL_SERVER, EMAIL_USERNAME and EMAIL_PASSWORD must all be set "
            "to send mail (see .env.example)."
        )
    message = EmailMessage()
    message.set_content(body)
    message["Subject"] = subject
    message["From"] = FROM_ADDRESS
    message["To"] = to_address
    if reply_to:
        message["Reply-To"] = reply_to

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(host, 465, context=context) as server:
        server.login(username, password)
        server.send_message(message)
