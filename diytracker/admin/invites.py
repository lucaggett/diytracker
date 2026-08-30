"""Invite emails in Schwiizerdütsch, English, or French.

The texts live in a plain dict rather than the flask-babel catalogs:
babel.cfg only scans app code/templates, emails were never localized
through it, and there is no 'gsw' catalog. The language is picked per
send (CLI --lang / TUI radio buttons), not stored on the Submitter.
"""

from diytracker.admin.core import AdminError
from diytracker.services.mail import send_mail

INVITE_LANGS = ("gsw", "en", "fr")
DEFAULT_INVITE_LANG = "gsw"

INVITE_MESSAGES = {
    "gsw": {
        "subject": "Willkomme bim diytracker!",
        "body": (
            "Sali!\n\n"
            "Du chasch jetzt im diytracker dis konto aalege."
            "Unter em folgende link chasch dis passwort setze (de Link isch 7 Täg gültig):\n"
            "https://diytracker.ch/set-password/{token}\n\n"
            "Wenn öppis nöd klappt oder du Frage hesch, "
            "antworte eifach uf das mail.\n\n"
            "Grüessli vom diytracker applikations-server"
        ),
    },
    "en": {
        "subject": "Set your diytracker.ch password",
        "body": (
            "You've been invited to diytracker.ch\n\n"
            "Set your password using this link (valid for 7 days):\n"
            "https://diytracker.ch/set-password/{token}\n\n"
            "If you have any questions, contact Luc at luc@aggett.com or reply to this email\n\n"
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


def invite_link(token):
    return f"https://diytracker.ch/set-password/{token}"


def send_invite_email(email, token, lang=DEFAULT_INVITE_LANG):
    if lang not in INVITE_MESSAGES:
        raise AdminError(
            f"Unknown invite language {lang!r} (expected one of {INVITE_LANGS})."
        )
    text = INVITE_MESSAGES[lang]
    send_mail(email, text["subject"], text["body"].format(token=token))
