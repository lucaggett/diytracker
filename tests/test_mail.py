"""The one SMTP path every mail in the app goes through."""

import pytest

from diytracker.services import mail


class TestSendMail:
    def test_opens_implicit_tls_and_logs_in(self, fake_smtp):
        mail.send_mail("someone@example.com", "Subject", "Body")

        smtp = fake_smtp.instances[0]
        assert smtp.host == "smtp.example.com"
        assert smtp.port == 465
        assert smtp.login_args == ("bot@example.com", "hunter2")

    def test_sets_the_standard_headers(self, fake_smtp):
        mail.send_mail("someone@example.com", "Subject", "Body")

        msg = fake_smtp.instances[0].sent_message
        assert msg["To"] == "someone@example.com"
        assert msg["From"] == mail.FROM_ADDRESS
        assert msg["Subject"] == "Subject"
        assert msg.get_content().strip() == "Body"
        assert msg["Reply-To"] is None

    def test_reply_to_is_optional(self, fake_smtp):
        mail.send_mail("a@example.com", "S", "B", reply_to="b@example.com")
        assert fake_smtp.instances[0].sent_message["Reply-To"] == "b@example.com"

    @pytest.mark.parametrize(
        "missing", ["EMAIL_SERVER", "EMAIL_USERNAME", "EMAIL_PASSWORD"]
    )
    def test_any_missing_credential_is_not_configured(
        self, fake_smtp, monkeypatch, missing
    ):
        # Distinguishable from an SMTP failure: callers that swallow send
        # errors still want "mail was never set up" to read differently.
        monkeypatch.delenv(missing, raising=False)
        with pytest.raises(mail.MailNotConfigured):
            mail.send_mail("a@example.com", "S", "B")
        assert fake_smtp.instances == []


class TestBuildLogger:
    def test_creates_the_log_dir_and_attaches_one_handler(self, tmp_path, monkeypatch):
        monkeypatch.setattr(mail, "LOGS_DIR", tmp_path / "logs")
        mail.logging.getLogger("diytracker.probe").handlers.clear()

        logger = mail.build_logger("probe", "probe.log")
        again = mail.build_logger("probe", "probe.log")

        assert logger is again
        assert len(logger.handlers) == 1  # not re-attached on the second call
        assert (tmp_path / "logs").exists()
        assert logger.propagate is False
