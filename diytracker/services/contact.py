"""The /about collaborator-request form's mail path.

SMTP itself lives in services/mail.py; what stays here is the one thing that
is specific to this form — who the message goes to, and the German subject
line the inbox filters on.
"""

from diytracker.services.mail import build_logger, send_mail

CONTACT_RECIPIENT = "luc@aggett.com"


def build_contact_logger():
    return build_logger("contact", "contact_form.log")


def send_contact_email(name, sender_email, message):
    """Send a collaborator-request email. Raises on misconfig or SMTP failure.

    Reply-To is the sender, so answering the notification answers the person
    who filled the form — the From stays our own account, which is what the
    SPF/DKIM records cover.
    """
    send_mail(
        CONTACT_RECIPIENT,
        f"[diytracker] Mitwirkenden-Anfrage von {name}",
        f"Name:    {name}\nE-Mail:  {sender_email}\n\nNachricht:\n{message}\n",
        reply_to=sender_email,
    )
