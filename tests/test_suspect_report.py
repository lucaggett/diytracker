"""The regular suspicious-traffic report (services/suspect_report.py)."""

import json
from datetime import datetime, timedelta

import pytest
from conftest import BrokenSMTP

from diytracker.models import ScrapeSuspect, db
from diytracker.services import suspect_report

NOW = datetime(2026, 10, 21, 12, 0)

LOG_LINES = [
    # Refused language switches: two IPs in one /16, one in another.
    '187.189.164.26 - - [20/Oct/2026:07:12:56 +0000] "GET /set-language/de?next=%2F HTTP/2.0" 403 57 "-" "Mozilla/5.0"',
    '187.189.1.2 - - [20/Oct/2026:07:13:00 +0000] "GET /set-language/it?next=%2F HTTP/2.0" 403 57 "-" "Mozilla/5.0"',
    '45.232.63.247 - - [20/Oct/2026:08:04:02 +0000] "GET /set-language/fr HTTP/2.0" 403 57 "-" "Mozilla/5.0"',
    # Rate limited.
    '85.93.89.122 - - [19/Oct/2026:10:00:00 +0000] "GET /events/1/ HTTP/2.0" 429 50 "-" "Mozilla/5.0"',
    '85.93.89.122 - - [19/Oct/2026:10:00:01 +0000] "GET /events/2/ HTTP/2.0" 429 50 "-" "Mozilla/5.0"',
    # Noise that must not count: an accepted switch, a 403 elsewhere, and a
    # refusal outside the window.
    '10.0.0.1 - - [20/Oct/2026:09:00:00 +0000] "GET /set-language/fr HTTP/2.0" 302 0 "https://diytracker.ch/" "Mozilla/5.0"',
    '10.0.0.2 - - [20/Oct/2026:09:00:00 +0000] "GET /admin/ HTTP/2.0" 403 10 "-" "Mozilla/5.0"',
    '10.0.0.3 - - [01/Oct/2026:09:00:00 +0000] "GET /set-language/fr HTTP/2.0" 403 57 "-" "Mozilla/5.0"',
]


@pytest.fixture
def access_log(tmp_path):
    path = tmp_path / "access.log"
    path.write_text("\n".join(LOG_LINES) + "\n")
    return path


@pytest.fixture
def report_state(tmp_path, monkeypatch):
    """Point the last-sent file at tmp and the log lookup at nothing."""
    monkeypatch.setattr(suspect_report, "LAST_REPORT_FILE", tmp_path / "last.txt")
    monkeypatch.setattr(suspect_report, "find_log_files", list)
    return tmp_path / "last.txt"


def _suspect(ip, first_seen, last_seen, score=6.0, **kw):
    row = ScrapeSuspect(
        ip=ip,
        first_seen=first_seen,
        last_seen=last_seen,
        score=score,
        request_count=kw.pop("request_count", 120),
        signals=json.dumps(kw.pop("signals", ["high-rate", "no-navigation"])),
        user_agent=kw.pop("user_agent", "python-requests/2.32"),
        sample_paths=json.dumps(kw.pop("sample_paths", ["/events/1/", "/events/2/"])),
    )
    db.session.add(row)
    db.session.commit()
    return row


class TestScanLogs:
    def test_counts_refusals_and_rate_limits_in_window(self, access_log):
        result = suspect_report.scan_logs(
            [access_log], NOW.date() - timedelta(days=7), NOW.date()
        )
        assert result["refused"] == 3
        assert result["refused_ips"] == 3
        assert result["refused_networks"].most_common(1) == [("187.189.0.0/16", 2)]
        assert result["rate_limited"] == {"85.93.89.122": 2}


class TestBuildReport:
    def test_lists_suspects_seen_in_window_only(self, app, access_log):
        _suspect("203.0.113.4", NOW - timedelta(days=2), NOW - timedelta(hours=1))
        _suspect(
            "203.0.113.5", NOW - timedelta(days=30), NOW - timedelta(days=1), score=4.5
        )
        _suspect("203.0.113.6", NOW - timedelta(days=40), NOW - timedelta(days=20))
        subject, body = suspect_report.build_report(7, now=NOW, log_files=[access_log])

        assert "14.10.2026 – 21.10.2026" in subject
        assert "MARKIERT: 2 IPs (1 neu)" in body
        assert "203.0.113.4  Score 6  120 Anfragen  neu" in body
        assert "203.0.113.5  Score 4.5  120 Anfragen\n" in body
        assert "203.0.113.6" not in body
        assert "Signale: high-rate, no-navigation" in body
        # Highest score first.
        assert body.index("203.0.113.4") < body.index("203.0.113.5")

    def test_includes_log_sections(self, app, access_log):
        _, body = suspect_report.build_report(7, now=NOW, log_files=[access_log])
        assert "3 Anfragen von 3 IPs" in body
        assert "187.189.0.0/16  2" in body
        assert "GEDROSSELT (429): 2 Anfragen von 1 IP\n" in body
        assert "85.93.89.122  2" in body

    def test_empty_week_still_reports(self, app):
        _, body = suspect_report.build_report(7, now=NOW, log_files=[])
        assert "MARKIERT: 0 IPs" in body
        assert "Keine." in body
        assert "keine nginx-Logs gefunden" in body

    def test_caps_the_suspect_list(self, app, monkeypatch):
        monkeypatch.setattr(suspect_report, "MAX_SUSPECTS", 2)
        for i in range(5):
            _suspect(f"203.0.113.{i}", NOW - timedelta(days=1), NOW)
        _, body = suspect_report.build_report(7, now=NOW, log_files=[])
        assert "… und 3 weitere." in body


class TestSchedule:
    def test_first_check_sends_and_records(self, app, report_state, fake_smtp):
        assert suspect_report.maybe_send_report(app, now=NOW) is True
        msg = fake_smtp.instances[0].sent_message
        assert msg["To"] == "info@diytracker.ch"
        assert msg["From"] == "info@diytracker.ch"
        assert "verdächtige Zugriffe" in msg["Subject"]
        assert suspect_report.get_last_report_time() == NOW

    def test_not_resent_within_interval(self, app, report_state, fake_smtp):
        suspect_report.set_last_report_time(NOW - timedelta(days=6, hours=23))
        assert suspect_report.maybe_send_report(app, now=NOW) is False
        assert fake_smtp.instances == []

    def test_resent_once_interval_passes(self, app, report_state, fake_smtp):
        suspect_report.set_last_report_time(NOW - timedelta(days=7))
        assert suspect_report.maybe_send_report(app, now=NOW) is True
        assert len(fake_smtp.instances) == 1

    def test_smtp_failure_stays_due(self, app, report_state, fake_smtp):
        fake_smtp.use(BrokenSMTP)
        assert suspect_report.maybe_send_report(app, now=NOW) is False
        assert suspect_report.get_last_report_time() is None

    def test_missing_mail_setup_skips_the_period(self, app, report_state, monkeypatch):
        for name in ("EMAIL_SERVER", "EMAIL_USERNAME", "EMAIL_PASSWORD"):
            monkeypatch.delenv(name, raising=False)
        assert suspect_report.maybe_send_report(app, now=NOW) is True
        assert suspect_report.get_last_report_time() == NOW

    def test_interval_zero_disables(self, app, report_state, fake_smtp, monkeypatch):
        monkeypatch.setitem(app.config, "SUSPECT_REPORT_INTERVAL_DAYS", 0)
        assert suspect_report.maybe_send_report(app, now=NOW) is False
        assert fake_smtp.instances == []

    def test_recipient_is_configurable(self, app, report_state, fake_smtp, monkeypatch):
        monkeypatch.setitem(app.config, "SUSPECT_REPORT_TO", "ops@example.com")
        suspect_report.maybe_send_report(app, now=NOW)
        assert fake_smtp.instances[0].sent_message["To"] == "ops@example.com"
