"""Tests for the contact-form email service.

SMTP mechanics (host, port, credentials, the not-configured error) belong to
services/mail.py and are covered in test_mail.py; what is asserted here is
what this module still decides on its own — recipient, subject and Reply-To.
"""

import pytest

from diytracker.services import contact, mail


class TestSendContactEmail:
    def test_sends_to_the_contact_address_with_reply_to_the_sender(self, fake_smtp):
        contact.send_contact_email("Jane", "jane@example.com", "Hi there")

        msg = fake_smtp.instances[0].sent_message
        assert msg["To"] == contact.CONTACT_RECIPIENT
        assert msg["Reply-To"] == "jane@example.com"
        assert "Jane" in msg["Subject"]
        assert "Hi there" in msg.get_content()

    def test_raises_when_env_missing(self, monkeypatch):
        monkeypatch.delenv("EMAIL_SERVER", raising=False)
        monkeypatch.delenv("EMAIL_USERNAME", raising=False)
        monkeypatch.delenv("EMAIL_PASSWORD", raising=False)
        with pytest.raises(mail.MailNotConfiguredError):
            contact.send_contact_email("Jane", "jane@example.com", "Hi")

    def test_propagates_smtp_errors(self, fake_smtp):
        class _BoomSMTP(fake_smtp):
            def login(self, username, password):
                raise mail.smtplib.SMTPAuthenticationError(535, b"bad creds")

        fake_smtp.use(_BoomSMTP)
        with pytest.raises(mail.smtplib.SMTPAuthenticationError):
            contact.send_contact_email("Jane", "jane@example.com", "Hi")


class TestBuildContactLogger:
    def test_returns_logger_with_file_handler(self, tmp_path, monkeypatch):
        monkeypatch.setattr(mail, "LOGS_DIR", tmp_path / "logs")
        # The underlying logger is a process-wide singleton keyed by name;
        # clear any handlers a prior call (in this or another test) attached
        # so this call re-runs the LOGS_DIR-creation path being tested.
        mail.logging.getLogger("diytracker.contact").handlers.clear()

        logger = contact.build_contact_logger()

        assert logger.handlers
        assert (tmp_path / "logs").exists()
