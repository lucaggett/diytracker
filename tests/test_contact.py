"""Tests for the contact-form email service."""

import pytest

from diytracker.services import contact


class _FakeSMTP:
    """Stand-in for smtplib.SMTP_SSL as a context manager."""

    instances = []

    def __init__(self, host, port, context=None):
        self.host = host
        self.port = port
        self.login_args = None
        self.sent_message = None
        _FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def login(self, username, password):
        self.login_args = (username, password)

    def send_message(self, msg):
        self.sent_message = msg


@pytest.fixture(autouse=True)
def _clear_instances():
    _FakeSMTP.instances.clear()
    yield
    _FakeSMTP.instances.clear()


class TestSendContactEmail:
    def test_sends_via_smtp_with_expected_fields(self, monkeypatch):
        monkeypatch.setenv("EMAIL_SERVER", "smtp.example.com")
        monkeypatch.setenv("EMAIL_USERNAME", "bot@example.com")
        monkeypatch.setenv("EMAIL_PASSWORD", "hunter2")
        monkeypatch.setattr(contact.smtplib, "SMTP_SSL", _FakeSMTP)

        contact.send_contact_email("Jane", "jane@example.com", "Hi there")

        smtp = _FakeSMTP.instances[0]
        assert smtp.host == "smtp.example.com"
        assert smtp.port == 465
        assert smtp.login_args == ("bot@example.com", "hunter2")
        msg = smtp.sent_message
        assert msg["To"] == contact.CONTACT_RECIPIENT
        assert msg["Reply-To"] == "jane@example.com"
        assert "Jane" in msg["Subject"]
        assert "Hi there" in msg.get_content()

    def test_raises_when_env_missing(self, monkeypatch):
        monkeypatch.delenv("EMAIL_SERVER", raising=False)
        monkeypatch.delenv("EMAIL_USERNAME", raising=False)
        monkeypatch.delenv("EMAIL_PASSWORD", raising=False)
        with pytest.raises(KeyError):
            contact.send_contact_email("Jane", "jane@example.com", "Hi")

    def test_propagates_smtp_errors(self, monkeypatch):
        monkeypatch.setenv("EMAIL_SERVER", "smtp.example.com")
        monkeypatch.setenv("EMAIL_USERNAME", "bot@example.com")
        monkeypatch.setenv("EMAIL_PASSWORD", "hunter2")

        class _BoomSMTP(_FakeSMTP):
            def login(self, username, password):
                raise contact.smtplib.SMTPAuthenticationError(535, b"bad creds")

        monkeypatch.setattr(contact.smtplib, "SMTP_SSL", _BoomSMTP)
        with pytest.raises(contact.smtplib.SMTPAuthenticationError):
            contact.send_contact_email("Jane", "jane@example.com", "Hi")


class TestBuildContactLogger:
    def test_returns_logger_with_file_handler(self, tmp_path, monkeypatch):
        monkeypatch.setattr(contact, "LOGS_DIR", tmp_path / "logs")
        # The underlying logger is a process-wide singleton keyed by name;
        # clear any handlers a prior call (in this or another test) attached
        # so this call re-runs the LOGS_DIR-creation path being tested.
        existing = contact.logging.getLogger("diytracker.contact")
        existing.handlers.clear()

        logger = contact.build_contact_logger()

        assert logger.handlers
        assert (tmp_path / "logs").exists()
