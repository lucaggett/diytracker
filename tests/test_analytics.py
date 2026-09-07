"""Tests for the goaccess report generation service."""

import json
import pathlib
from datetime import date, datetime, timedelta

from diytracker.services import analytics


def _panel_entry(day, hits, visitors):
    return {
        "data": day.isoformat(),
        "hits": {"count": hits},
        "visitors": {"count": visitors},
    }


def _fake_popen(captured, returncode=0, stderr_text="", on_wait=None):
    """Stand-in for subprocess.Popen as analytics._run_goaccess drives it.

    The helper streams into stdin rather than handing subprocess one big
    string (an "all time" report over a production log dir is hundreds of MB),
    so the fake accumulates the written chunks into captured["input"]. stderr
    is the temp file the helper opened, which is where its error path reads
    goaccess's complaint back from.
    """

    class _Stdin:
        def write(self, chunk):
            captured.setdefault("input", []).append(chunk)

        def close(self):
            captured["stdin_closed"] = True

    class _FakePopen:
        def __init__(self, argv, stdin=None, stdout=None, stderr=None, **kwargs):
            captured["cmd"] = argv
            self.stdin = _Stdin()
            self._stderr = stderr
            self.returncode = returncode

        def wait(self, timeout=None):
            if stderr_text:
                self._stderr.write(stderr_text)
            if on_wait is not None:
                on_wait(captured["cmd"])
            return self.returncode

    return _FakePopen


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
            analytics,
            "Path",
            lambda p: prod_dir if p == "/var/log/nginx" else pathlib.Path(p),
        )
        files = analytics.find_log_files()
        names = sorted(f.name for f in files)
        assert names == ["access.log", "access.log.1.gz"]

    def test_returns_empty_when_nothing_found(self, tmp_path, monkeypatch):
        monkeypatch.setattr(analytics, "BASE_DIR", tmp_path)
        monkeypatch.setattr(
            analytics,
            "Path",
            lambda p: (
                tmp_path / "nonexistent" if p == "/var/log/nginx" else pathlib.Path(p)
            ),
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


class TestIsAdminRequest:
    def test_admin_paths_are_flagged(self):
        for path in ["/admin", "/admin/analytics/stats", "/admin/edit_event/1?x=1"]:
            line = f'1.2.3.4 - - [03/Jul/2026:12:00:00 +0000] "GET {path} HTTP/2.0" 200 458 "https://diytracker.ch/admin" "Mozilla/5.0"'
            assert analytics._is_admin_request(line) is True

    def test_public_paths_pass_through(self):
        for path in ["/", "/about", "/administrivia", "/map?canton=ZH"]:
            line = f'1.2.3.4 - - [03/Jul/2026:12:00:00 +0000] "GET {path} HTTP/2.0" 200 458 "-" "Mozilla/5.0"'
            assert analytics._is_admin_request(line) is False

    def test_malformed_line_passes_through(self):
        assert analytics._is_admin_request("no quotes here") is False
        assert analytics._is_admin_request('x - - "GARBAGE" 200') is False

    def test_iter_filtered_lines_drops_admin_traffic(self, tmp_path):
        log = tmp_path / "access.log"
        log.write_text(
            'x - - [03/Jul/2026:00:00:00 +0000] "GET / HTTP/1.1" 200 1\n'
            'x - - [03/Jul/2026:00:01:00 +0000] "GET /admin/analytics/stats HTTP/2.0" 200 458\n'
            'x - - [03/Jul/2026:00:02:00 +0000] "GET /about HTTP/1.1" 200 1\n'
        )
        lines = list(analytics._iter_filtered_lines([log], None))
        assert len(lines) == 2
        assert all("/admin" not in line for line in lines)


class TestGenerateReport:
    def test_fails_when_goaccess_missing(self, monkeypatch):
        monkeypatch.setattr(analytics.shutil, "which", lambda name: None)
        success, message = analytics.generate_report("7d")
        assert success is False
        assert "goaccess not found" in message

    def test_fails_when_no_log_files(self, monkeypatch):
        monkeypatch.setattr(analytics.shutil, "which", lambda name: "/usr/bin/goaccess")
        monkeypatch.setattr(analytics, "find_log_files", list)
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

        def write_report(_cmd):
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text("<html>report</html>")

        monkeypatch.setattr(
            analytics.subprocess, "Popen", _fake_popen(captured, on_wait=write_report)
        )
        success, message = analytics.generate_report("all")
        assert success is True
        assert "Report generated" in message
        assert report_path.exists()
        assert "GET / HTTP/1.1" in "".join(captured["input"])
        assert captured["stdin_closed"] is True

    def test_reports_goaccess_error(self, tmp_path, monkeypatch):
        report_path = tmp_path / "nginx_report.html"
        monkeypatch.setattr(analytics, "REPORT_PATH", report_path)
        monkeypatch.setattr(analytics.shutil, "which", lambda name: "/usr/bin/goaccess")
        log = tmp_path / "access.log"
        log.write_text('x - - [03/Jul/2026:00:00:00 +0000] "GET / HTTP/1.1" 200 1\n')
        monkeypatch.setattr(analytics, "find_log_files", lambda: [log])
        monkeypatch.setattr(
            analytics.subprocess,
            "Popen",
            _fake_popen({}, returncode=1, stderr_text="boom"),
        )
        success, message = analytics.generate_report("all")
        assert success is False
        assert message == "goaccess error: boom"


class TestParsePanelDate:
    def test_supported_formats(self):
        expected = date(2026, 7, 3)
        assert analytics._parse_panel_date("2026-07-03") == expected
        assert analytics._parse_panel_date("20260703") == expected
        assert analytics._parse_panel_date("03/Jul/2026") == expected

    def test_unknown_format_returns_none(self):
        assert analytics._parse_panel_date("July 3rd") is None


class TestParseGoaccessJson:
    def test_window_totals_growth_and_daily(self):
        today = date(2026, 7, 13)
        data = {
            "visitors": {
                "data": [
                    _panel_entry(today, 10, 2),
                    _panel_entry(today - timedelta(days=1), 8, 3),
                    _panel_entry(today - timedelta(days=5), 20, 4),
                    _panel_entry(today - timedelta(days=40), 100, 10),
                ]
            }
        }
        stats = analytics._parse_goaccess_json(data, today)

        assert stats["totals"]["today"] == {"hits": 10, "visitors": 2}
        assert stats["totals"]["yesterday"] == {"hits": 8, "visitors": 3}
        assert stats["totals"]["last_7d"] == {"hits": 38, "visitors": 9}
        assert stats["totals"]["last_30d"] == {"hits": 38, "visitors": 9}
        assert stats["growth_30d"]["prev_30d"] == {"hits": 100, "visitors": 10}
        assert stats["growth_30d"]["hits_pct"] == -62.0
        assert stats["growth_30d"]["visitors_pct"] == -10.0

        daily = stats["daily"]
        assert len(daily) == 30
        assert daily[-1] == {"date": today.isoformat(), "hits": 10, "visitors": 2}
        # Gap days are zero-filled.
        assert daily[-3] == {
            "date": (today - timedelta(days=2)).isoformat(),
            "hits": 0,
            "visitors": 0,
        }

    def test_growth_none_when_previous_window_empty(self):
        today = date(2026, 7, 13)
        data = {"visitors": {"data": [_panel_entry(today, 5, 1)]}}
        stats = analytics._parse_goaccess_json(data, today)
        assert stats["growth_30d"]["hits_pct"] is None
        assert stats["growth_30d"]["visitors_pct"] is None

    def test_skips_unparseable_dates(self):
        today = date(2026, 7, 13)
        data = {
            "visitors": {
                "data": [
                    {"data": "???", "hits": {"count": 9}, "visitors": {"count": 9}},
                    _panel_entry(today, 5, 1),
                ]
            }
        }
        stats = analytics._parse_goaccess_json(data, today)
        assert stats["totals"]["today"] == {"hits": 5, "visitors": 1}


class TestGenerateStats:
    def test_fails_when_goaccess_missing(self, monkeypatch):
        monkeypatch.setattr(analytics.shutil, "which", lambda name: None)
        success, message = analytics.generate_stats()
        assert success is False
        assert "goaccess not found" in message

    def test_fails_when_no_log_files(self, monkeypatch):
        monkeypatch.setattr(analytics.shutil, "which", lambda name: "/usr/bin/goaccess")
        monkeypatch.setattr(analytics, "find_log_files", list)
        success, message = analytics.generate_stats()
        assert success is False
        assert "No nginx access log files found" in message

    def test_keeps_cache_when_no_entries_in_window(self, tmp_path, monkeypatch):
        stats_path = tmp_path / "analytics_stats.json"
        stats_path.write_text('{"old": true}')
        monkeypatch.setattr(analytics, "STATS_PATH", stats_path)
        monkeypatch.setattr(analytics.shutil, "which", lambda name: "/usr/bin/goaccess")
        log = tmp_path / "access.log"
        log.write_text('x - - [01/Jan/2000:00:00:00 +0000] "GET / HTTP/1.1" 200 1\n')
        monkeypatch.setattr(analytics, "find_log_files", lambda: [log])
        success, message = analytics.generate_stats()
        assert success is False
        assert "No log entries found" in message
        assert json.loads(stats_path.read_text()) == {"old": True}

    def test_success_writes_stats_json(self, tmp_path, monkeypatch):
        stats_path = tmp_path / "instance" / "analytics_stats.json"
        monkeypatch.setattr(analytics, "STATS_PATH", stats_path)
        monkeypatch.setattr(analytics.shutil, "which", lambda name: "/usr/bin/goaccess")
        today = datetime.now().date()
        log = tmp_path / "access.log"
        log.write_text(
            f'x - - [{today.strftime("%d/%b/%Y")}:00:00:00 +0000] "GET / HTTP/1.1" 200 1\n'
        )
        monkeypatch.setattr(analytics, "find_log_files", lambda: [log])

        def write_json(cmd):
            out = next(a for a in cmd if a.startswith("--output=")).split("=", 1)[1]
            fixture = {"visitors": {"data": [_panel_entry(today, 7, 3)]}}
            pathlib.Path(out).write_text(json.dumps(fixture))

        monkeypatch.setattr(
            analytics.subprocess, "Popen", _fake_popen({}, on_wait=write_json)
        )
        success, message = analytics.generate_stats()
        assert success is True
        assert "Stats generated" in message
        stats = json.loads(stats_path.read_text())
        assert stats["totals"]["today"] == {"hits": 7, "visitors": 3}
        assert len(stats["daily"]) == 30
        # The raw goaccess output is cleaned up.
        assert not stats_path.with_name("analytics_stats_raw.json").exists()

    def test_reports_goaccess_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(analytics, "STATS_PATH", tmp_path / "stats.json")
        monkeypatch.setattr(analytics.shutil, "which", lambda name: "/usr/bin/goaccess")
        today = datetime.now().date()
        log = tmp_path / "access.log"
        log.write_text(
            f'x - - [{today.strftime("%d/%b/%Y")}:00:00:00 +0000] "GET / HTTP/1.1" 200 1\n'
        )
        monkeypatch.setattr(analytics, "find_log_files", lambda: [log])
        monkeypatch.setattr(
            analytics.subprocess,
            "Popen",
            _fake_popen({}, returncode=1, stderr_text="boom"),
        )
        success, message = analytics.generate_stats()
        assert success is False
        assert message == "goaccess error: boom"


class TestReadStats:
    def test_missing_file_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(analytics, "STATS_PATH", tmp_path / "missing.json")
        assert analytics.read_stats() is None
        assert analytics.stats_age_seconds() is None

    def test_corrupt_file_returns_none(self, tmp_path, monkeypatch):
        path = tmp_path / "stats.json"
        path.write_text("{not json")
        monkeypatch.setattr(analytics, "STATS_PATH", path)
        assert analytics.read_stats() is None

    def test_valid_file_round_trips(self, tmp_path, monkeypatch):
        path = tmp_path / "stats.json"
        path.write_text('{"window_days": 30}')
        monkeypatch.setattr(analytics, "STATS_PATH", path)
        assert analytics.read_stats() == {"window_days": 30}
        assert analytics.stats_age_seconds() < 60
