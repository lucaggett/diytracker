"""Invite emails in Schwiizerdütsch, English, or French.

The texts live in a plain dict rather than the flask-babel catalogs:
babel.cfg only scans app code/templates, emails were never localized
through it, and there is no 'gsw' catalog. The language is picked per
send (CLI --lang / TUI radio buttons), not stored on the Submitter.
"""

import os
import smtplib
import ssl
from email.message import EmailMessage

from diytracker.admin.core import AdminError

INVITE_LANGS = ("gsw", "en", "fr")
DEFAULT_INVITE_LANG = "gsw"

INVITE_MESSAGES = {
    "gsw": {
        "subject": "Dis Passwort für diytracker.ch",
        "body": (
            "Hoi!\n\n"
            "Du bisch iglade worde uf diytracker.ch – de Kaländer für "
            "DIY-Konzärt und -Events i de Schwiiz.\n\n"
            "Do chasch dis Passwort setze (de Link isch 7 Täg gültig):\n"
            "https://diytracker.ch/set-password/{token}\n\n"
            "Wenn öppis nöd klappt oder du Frage hesch, "
            "schriib em Luc: luc@aggett.com\n\n"
            "Bis gli am nächschte Gig!"
        ),
    },
    "en": {
        "subject": "Set your diytracker.ch password",
        "body": (
            "You've been invited to diytracker.ch – the calendar for DIY "
            "shows and events in Switzerland!\n\n"
            "Set your password using this link (valid for 7 days):\n"
            "https://diytracker.ch/set-password/{token}\n\n"
            "If you have any questions, contact Luc at luc@aggett.com\n\n"
            "See you at a show!"
        ),
    },
    "fr": {
        "subject": "Définis ton mot de passe diytracker.ch",
        "body": (
            "Salut !\n\n"
            "Tu as été invité·e sur diytracker.ch – le calendrier des "
            "concerts et événements DIY en Suisse.\n\n"
            "Définis ton mot de passe via ce lien (valable 7 jours) :\n"
            "https://diytracker.ch/set-password/{token}\n\n"
            "Pour toute question, écris à Luc : luc@aggett.com\n\n"
            "À bientôt aux concerts !"
        ),
    },
}

LANG_LABELS = {"gsw": "Schwiizerdütsch", "en": "English", "fr": "Français"}


def send_email(to_address, subject, body):
    message = EmailMessage()
    message.set_content(body)
    message["Subject"] = subject
    message["From"] = "info@diytracker.ch"
    message["To"] = to_address

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(os.environ["EMAIL_SERVER"], 465, context=context) as server:
        server.login(os.environ["EMAIL_USERNAME"], os.environ["EMAIL_PASSWORD"])
        server.send_message(message)


def invite_link(token):
    return f"https://diytracker.ch/set-password/{token}"


def send_invite_email(email, token, lang=DEFAULT_INVITE_LANG):
    if lang not in INVITE_MESSAGES:
        raise AdminError(
            f"Unknown invite language {lang!r} (expected one of {INVITE_LANGS})."
        )
    text = INVITE_MESSAGES[lang]
    send_email(email, text["subject"], text["body"].format(token=token))
