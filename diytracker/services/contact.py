import logging
import os
import smtplib
import ssl
from email.message import EmailMessage

from diytracker.paths import LOGS_DIR

CONTACT_RECIPIENT = "luc@aggett.com"


def build_contact_logger():
    logger = logging.getLogger("diytracker.contact")
    if not logger.handlers:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(LOGS_DIR / "contact_form.log")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger


def send_contact_email(name, sender_email, message):
    """Send a collaborator-request email. Raises on misconfig or SMTP failure."""
    server_host = os.environ["EMAIL_SERVER"]
    username = os.environ["EMAIL_USERNAME"]
    password = os.environ["EMAIL_PASSWORD"]

    msg = EmailMessage()
    msg["Subject"] = f"[diytracker] Mitwirkenden-Anfrage von {name}"
    msg["From"] = "info@diytracker.ch"
    msg["To"] = CONTACT_RECIPIENT
    msg["Reply-To"] = sender_email
    msg.set_content(
        f"Name:    {name}\nE-Mail:  {sender_email}\n\nNachricht:\n{message}\n"
    )

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(server_host, 465, context=context) as server:
        server.login(username, password)
        server.send_message(msg)
