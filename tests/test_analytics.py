"""Tests for the goaccess report generation service."""

import pathlib
from datetime import datetime, timedelta

from diytracker.services import analytics


class _FakeCompleted:
    def __init__(self, returncode=0, stderr=""):
        self.returncode = returncode
        self.stderr = stderr


class TestFindLogFiles:
    def test_prefers_dev_log_when_present(self, tmp_path, monkeypatch):
        monkeypatch.setattr(analytics, "BASE_DIR", tmp_path)
        dev_dir = tmp_path / "logs"
        dev_dir.mkdir()
        dev_log = dev_dir / "nginx_access.log"
        dev_log.write_text("line\n")
        assert analytics.find_log_files() == [dev_log]

    def test_falls_back_to_prod_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(analytics, "BASE_DIR", tmp_path)  # no dev log here
        prod_dir = tmp_path / "prod_nginx"
        prod_dir.mkdir()
        (prod_dir / "access.log").write_text("a\n")
        (prod_dir / "access.log.1.gz").write_text("b\n")
        (prod_dir / "other.log").write_text("c\n")
        monkeypatch.setattr(
            analytics, "Path", lambda p: prod_dir if p == "/var/log/nginx" else pathlib.Path(p)
        )
        files = analytics.find_log_files()
        names = sorted(f.name for f in files)
        assert names == ["access.log", "access.log.1.gz"]

    def test_returns_empty_when_nothing_found(self, tmp_path, monkeypatch):
        monkeypatch.setattr(analytics, "BASE_DIR", tmp_path)
        monkeypatch.setattr(
            analytics,
            "Path",
            lambda p: tmp_path / "nonexistent" if p == "/var/log/nginx" else pathlib.Path(p),
        )
        assert analytics.find_log_files() == []


class TestDateRange:
    def test_today(self):
        today = datetime.now().date()
        assert analytics._date_range("today") == (today, today)

    def test_yesterday(self):
        today = datetime.now().date()
        assert analytics._date_range("yesterday") == (
            today - timedelta(days=1),
            today - timedelta(days=1),
        )

    def test_7d_and_30d_and_3mo(self):
        today = datetime.now().date()
        assert analytics._date_range("7d") == (today - timedelta(days=6), today)
        assert analytics._date_range("30d") == (today - timedelta(days=29), today)
        assert analytics._date_range("3mo") == (today - timedelta(days=89), today)

    def test_all_returns_none(self):
        assert analytics._date_range("all") == (None, None)
        assert analytics._date_range("unknown") == (None, None)


class TestBuildValidDates:
    def test_none_start_returns_none(self):
        assert analytics._build_valid_dates(None, None) is None

    def test_builds_inclusive_date_set(self):
        today = datetime.now().date()
        dates = analytics._build_valid_dates(today, today + timedelta(days=2))
        assert dates == {
            today.strftime("%d/%b/%Y"),
            (today + timedelta(days=1)).strftime("%d/%b/%Y"),
            (today + timedelta(days=2)).strftime("%d/%b/%Y"),
        }


class TestExtractLogDate:
    def test_extracts_bracketed_date(self):
        line = '127.0.0.1 - - [03/Jul/2026:12:00:00 +0000] "GET / HTTP/1.1" 200 100'
        assert analytics._extract_log_date(line) == "03/Jul/2026"

    def test_returns_none_when_no_bracket(self):
        assert analytics._extract_log_date("no brackets here") is None


class TestGenerateReport:
    def test_fails_when_goaccess_missing(self, monkeypatch):
        monkeypatch.setattr(analytics.shutil, "which", lambda name: None)
        success, message = analytics.generate_report("7d")
        assert success is False
        assert "goaccess not found" in message

    def test_fails_when_no_log_files(self, monkeypatch):
        monkeypatch.setattr(analytics.shutil, "which", lambda name: "/usr/bin/goaccess")
        monkeypatch.setattr(analytics, "find_log_files", lambda: [])
        success, message = analytics.generate_report("7d")
        assert success is False
        assert "No nginx access log files found" in message

    def test_fails_when_no_entries_for_timeframe(self, tmp_path, monkeypatch):
        monkeypatch.setattr(analytics.shutil, "which", lambda name: "/usr/bin/goaccess")
        log = tmp_path / "access.log"
        # Dated well outside the "today" window used below.
        log.write_text('x - - [01/Jan/2000:00:00:00 +0000] "GET / HTTP/1.1" 200 1\n')
        monkeypatch.setattr(analytics, "find_log_files", lambda: [log])
        success, message = analytics.generate_report("today")
        assert success is False
        assert "No log entries found" in message

    def test_success_runs_goaccess_and_writes_report(self, tmp_path, monkeypatch):
        report_path = tmp_path / "instance" / "nginx_report.html"
        monkeypatch.setattr(analytics, "REPORT_PATH", report_path)
        monkeypatch.setattr(analytics.shutil, "which", lambda name: "/usr/bin/goaccess")
        log = tmp_path / "access.log"
        log.write_text('x - - [03/Jul/2026:00:00:00 +0000] "GET / HTTP/1.1" 200 1\n')
        monkeypatch.setattr(analytics, "find_log_files", lambda: [log])

        captured = {}

        def fake_run(cmd, input, capture_output, text, env):
            captured["cmd"] = cmd
            captured["input"] = input
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text("<html>report</html>")
            return _FakeCompleted(returncode=0)

        monkeypatch.setattr(analytics.subprocess, "run", fake_run)
        success, message = analytics.generate_report("all")
        assert success is True
        assert "Report generated" in message
        assert report_path.exists()
        assert "GET / HTTP/1.1" in captured["input"]

    def test_reports_goaccess_error(self, tmp_path, monkeypatch):
        report_path = tmp_path / "nginx_report.html"
        monkeypatch.setattr(analytics, "REPORT_PATH", report_path)
        monkeypatch.setattr(analytics.shutil, "which", lambda name: "/usr/bin/goaccess")
        log = tmp_path / "access.log"
        log.write_text('x - - [03/Jul/2026:00:00:00 +0000] "GET / HTTP/1.1" 200 1\n')
        monkeypatch.setattr(analytics, "find_log_files", lambda: [log])
        monkeypatch.setattr(
            analytics.subprocess,
            "run",
            lambda *a, **kw: _FakeCompleted(returncode=1, stderr="boom"),
        )
        success, message = analytics.generate_report("all")
        assert success is False
        assert "goaccess error: boom" == message
