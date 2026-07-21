"""Tests for the admin traffic-analysis logic (diytracker/admin/traffic.py).

Logic layer only, like the rest of the admin modules — the Textual screens
have no driver tests. Log lines are synthesized in both formats the parser
must understand: quoted nginx COMBINED and gunicorn's unquoted variant.
"""

import gzip
import json
from datetime import date, datetime, timedelta

import pytest

from diytracker.admin import traffic
from diytracker.admin.core import AdminError
from diytracker.models import EventDailyViews, ScrapeSuspect, db

BROWSER_UA = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"


def nginx_line(ip, ts, path, status=200, ref="-", ua=BROWSER_UA):
    stamp = ts.strftime("%d/%b/%Y:%H:%M:%S +0200")
    return f'{ip} - - [{stamp}] "GET {path} HTTP/1.1" {status} 1234 "{ref}" "{ua}"'


def gunicorn_line(ip, ts, path, status=200, ref="-", ua=BROWSER_UA):
    stamp = ts.strftime("%d/%b/%Y:%H:%M:%S +0200")
    return f"{ip} - - [{stamp}] GET {path} HTTP/1.1 {status} 1234 {ref} {ua}"


@pytest.fixture
def log_dir(tmp_path, monkeypatch):
    """Point the traffic module at a tmp log file; returns a writer."""
    log_file = tmp_path / "access.log"

    def write(lines):
        log_file.write_text("\n".join(lines) + "\n")

    monkeypatch.setattr(traffic, "find_log_files", lambda: [log_file])
    return write


def browse(ip, minutes_apart=7, n=4, first_path="/"):
    """A believable human session: home page then a few event pages."""
    now = datetime.now()
    lines = [nginx_line(ip, now - timedelta(minutes=minutes_apart * n), first_path)]
    for i in range(n - 1):
        lines.append(
            nginx_line(
                ip,
                now - timedelta(minutes=minutes_apart * (n - 1 - i)),
                f"/events/{100 + i * 7}/",
            )
        )
    return lines


def profile_for(report, ip):
    return next(p for p in report.ips if p.ip == ip)


def signal_names(profile):
    return [s.split(" ")[0] for s in profile.signals]


class TestParsing:
    def test_reads_both_log_formats(self, app, log_dir):
        now = datetime.now()
        log_dir(
            [
                nginx_line("1.1.1.1", now, "/"),
                gunicorn_line("2.2.2.2", now, "/"),
                "not a log line at all",
            ]
        )
        with app.app_context():
            report = traffic.analyze("7d")
        assert report.total_requests == 2
        assert report.total_ips == 2

    def test_reads_gzipped_logs(self, app, tmp_path, monkeypatch):
        gz = tmp_path / "access.log.2.gz"
        with gzip.open(gz, "wt") as f:
            f.write(nginx_line("1.1.1.1", datetime.now(), "/") + "\n")
        monkeypatch.setattr(traffic, "find_log_files", lambda: [gz])
        with app.app_context():
            report = traffic.analyze("7d")
        assert report.total_requests == 1

    def test_skips_admin_requests_in_both_formats(self, app, log_dir):
        now = datetime.now()
        log_dir(
            [
                nginx_line("1.1.1.1", now, "/"),
                nginx_line("1.1.1.1", now, "/admin/statistics"),
                gunicorn_line("1.1.1.1", now, "/admin/statistics"),
            ]
        )
        with app.app_context():
            report = traffic.analyze("7d")
        assert report.total_requests == 1

    def test_timeframe_filters_old_lines(self, app, log_dir):
        now = datetime.now()
        log_dir(
            [
                nginx_line("1.1.1.1", now - timedelta(days=20), "/"),
                nginx_line("2.2.2.2", now, "/"),
            ]
        )
        with app.app_context():
            assert traffic.analyze("7d").total_ips == 1
            assert traffic.analyze("30d").total_ips == 2

    def test_ipv6_addresses_survive(self, app, log_dir):
        log_dir(browse("2a02:1210:2e8b:d800::1"))
        with app.app_context():
            report = traffic.analyze("7d")
        assert report.ips[0].ip == "2a02:1210:2e8b:d800::1"

    def test_no_log_files_raises(self, app, monkeypatch, tmp_path):
        monkeypatch.setattr(traffic, "find_log_files", lambda: [])
        monkeypatch.setattr(traffic, "ACCESS_LOG", tmp_path / "missing")
        with app.app_context():
            with pytest.raises(AdminError, match="No access logs"):
                traffic.analyze("7d")

    def test_empty_window_raises(self, app, log_dir):
        log_dir([nginx_line("1.1.1.1", datetime.now() - timedelta(days=30), "/")])
        with app.app_context():
            with pytest.raises(AdminError, match="No log entries"):
                traffic.analyze("7d")

    def test_unknown_timeframe_raises(self, app):
        with pytest.raises(AdminError, match="timeframe"):
            traffic.analyze("fortnight")


class TestSignals:
    def _analyze(self, app, log_dir, lines):
        log_dir(lines)
        with app.app_context():
            return traffic.analyze("7d")

    def test_scripted_ua(self, app, log_dir):
        report = self._analyze(
            app,
            log_dir,
            [nginx_line("1.1.1.1", datetime.now(), "/", ua="curl/8.7.1")] * 2,
        )
        assert "scripted-ua" in signal_names(report.ips[0])

    def test_missing_ua(self, app, log_dir):
        report = self._analyze(
            app, log_dir, [nginx_line("1.1.1.1", datetime.now(), "/", ua="-")]
        )
        assert "missing-ua" in signal_names(report.ips[0])

    def test_honeypot(self, app, log_dir):
        report = self._analyze(
            app,
            log_dir,
            [nginx_line("1.1.1.1", datetime.now(), "/events/archive/2023")],
        )
        p = report.ips[0]
        assert "honeypot" in signal_names(p)
        assert p.bucket == "bot"  # 5.0 alone crosses the threshold

    def test_probe_paths(self, app, log_dir):
        report = self._analyze(
            app,
            log_dir,
            [nginx_line("1.1.1.1", datetime.now(), "/wp-login.php", status=404)],
        )
        assert "probe-paths" in signal_names(report.ips[0])

    def test_ua_rotation(self, app, log_dir):
        now = datetime.now()
        lines = [
            nginx_line(
                "1.1.1.1", now - timedelta(minutes=i), "/", ua=f"{BROWSER_UA} v{i}"
            )
            for i in range(6)
        ]
        report = self._analyze(app, log_dir, lines)
        assert "ua-rotation" in signal_names(report.ips[0])

    def test_burst(self, app, log_dir):
        now = datetime.now()
        lines = [
            nginx_line("1.1.1.1", now + timedelta(milliseconds=400 * i), "/")
            for i in range(130)
        ]
        report = self._analyze(app, log_dir, lines)
        assert "burst" in signal_names(report.ips[0])

    def test_metronomic_timing(self, app, log_dir):
        now = datetime.now() - timedelta(minutes=10)
        lines = [
            nginx_line("1.1.1.1", now + timedelta(seconds=5 * i), f"/venues/{i}")
            for i in range(25)
        ]
        report = self._analyze(app, log_dir, lines)
        assert "metronomic" in signal_names(report.ips[0])

    def test_human_pacing_is_not_metronomic(self, app, log_dir):
        now = datetime.now() - timedelta(hours=2)
        gaps = [
            0,
            30,
            45,
            200,
            12,
            90,
            300,
            8,
            60,
            150,
            20,
            400,
            33,
            75,
            180,
            25,
            55,
            240,
            15,
            110,
            66,
            95,
            140,
            42,
            210,
        ]
        t = now
        lines = []
        for i, gap in enumerate(gaps):
            t += timedelta(seconds=gap)
            lines.append(nginx_line("1.1.1.1", t, "/" if i == 0 else f"/venues/{i}"))
        report = self._analyze(app, log_dir, lines)
        assert "metronomic" not in signal_names(report.ips[0])

    def test_event_id_enumeration(self, app, log_dir):
        now = datetime.now() - timedelta(minutes=30)
        lines = [
            nginx_line("1.1.1.1", now + timedelta(minutes=i), f"/events/{i + 1}/")
            for i in range(9)
        ]
        report = self._analyze(app, log_dir, lines)
        assert "enumeration" in signal_names(report.ips[0])

    def test_no_navigation(self, app, log_dir):
        now = datetime.now() - timedelta(minutes=30)
        lines = [
            nginx_line(
                "1.1.1.1", now + timedelta(minutes=i * 3), f"/events/{90 - i * 7}/"
            )
            for i in range(6)
        ]
        report = self._analyze(app, log_dir, lines)
        assert "no-navigation" in signal_names(report.ips[0])

    def test_internal_referer_counts_as_navigation(self, app, log_dir):
        now = datetime.now() - timedelta(minutes=30)
        lines = [
            nginx_line(
                "1.1.1.1",
                now + timedelta(minutes=i * 3),
                f"/events/{90 - i * 7}/",
                ref="https://diytracker.ch/",
            )
            for i in range(6)
        ]
        report = self._analyze(app, log_dir, lines)
        assert "no-navigation" not in signal_names(report.ips[0])

    def test_all_errors(self, app, log_dir):
        report = self._analyze(
            app,
            log_dir,
            [nginx_line("1.1.1.1", datetime.now(), "/nope", status=404)] * 3,
        )
        assert "all-errors" in signal_names(report.ips[0])

    def test_high_volume(self, app, log_dir):
        now = datetime.now() - timedelta(hours=20)
        lines = [
            nginx_line("1.1.1.1", now + timedelta(minutes=2 * i), f"/venues/{i % 30}")
            for i in range(500)
        ]
        report = self._analyze(app, log_dir, lines)
        assert "high-volume" in signal_names(report.ips[0])


class TestBuckets:
    def test_clean_browser_session_is_human(self, app, log_dir):
        log_dir(browse("1.1.1.1"))
        with app.app_context():
            report = traffic.analyze("7d")
        assert report.ips[0].bucket == "human"
        assert report.bucket_counts == {"human": 1}

    def test_declared_crawler_stays_crawler_even_when_scored(self, app, log_dir):
        now = datetime.now() - timedelta(minutes=30)
        ua = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
        lines = [
            nginx_line(
                "66.249.66.1", now + timedelta(minutes=i), f"/events/{i + 1}/", ua=ua
            )
            for i in range(9)
        ]
        log_dir(lines)
        with app.app_context():
            report = traffic.analyze("7d")
        assert report.ips[0].bucket == "crawler"

    def test_scripted_prober_is_bot(self, app, log_dir):
        now = datetime.now()
        log_dir(
            [
                nginx_line(
                    "1.1.1.1", now, "/.env", status=404, ua="python-requests/2.32"
                ),
                nginx_line("1.1.1.1", now, "/", status=200, ua="python-requests/2.32"),
            ]
        )
        with app.app_context():
            report = traffic.analyze("7d")
        assert report.ips[0].bucket == "bot"

    def test_single_request_is_unclear_not_human(self, app, log_dir):
        log_dir([nginx_line("1.1.1.1", datetime.now(), "/")])
        with app.app_context():
            report = traffic.analyze("7d")
        assert report.ips[0].bucket == "unclear"

    def test_bots_sort_before_humans(self, app, log_dir):
        lines = browse("9.9.9.9")
        lines.append(nginx_line("1.1.1.1", datetime.now(), "/events/archive/x"))
        log_dir(lines)
        with app.app_context():
            report = traffic.analyze("7d")
        assert [p.ip for p in report.ips] == ["1.1.1.1", "9.9.9.9"]

    def test_est_humans_feeds_day_trend(self, app, log_dir):
        lines = browse("9.9.9.9") + [
            nginx_line("1.1.1.1", datetime.now(), "/", ua="curl/8")
        ]
        log_dir(lines)
        with app.app_context():
            report = traffic.analyze("7d")
        assert sum(d.est_humans for d in report.days) == 1
        assert sum(d.requests for d in report.days) == report.total_requests


class TestSuspectCrossReference:
    def test_live_flagged_ip_is_marked(self, app, log_dir):
        log_dir(browse("5.5.5.5"))
        with app.app_context():
            db.session.add(
                ScrapeSuspect(
                    ip="5.5.5.5",
                    score=6.5,
                    request_count=120,
                    signals=json.dumps(["no_accept_language", "high_rate"]),
                )
            )
            db.session.commit()
            report = traffic.analyze("7d")
        p = profile_for(report, "5.5.5.5")
        assert p.suspect
        assert p.suspect_score == 6.5
        assert "no_accept_language" in p.suspect_signals

    def test_unflagged_ip_is_not_marked(self, app, log_dir):
        log_dir(browse("5.5.5.5"))
        with app.app_context():
            report = traffic.analyze("7d")
        assert not profile_for(report, "5.5.5.5").suspect


class TestEvents:
    def _seed(self, event_id, day_hits):
        for offset, hits in day_hits.items():
            db.session.add(
                EventDailyViews(
                    event_id=event_id,
                    date=date.today() - timedelta(days=offset),
                    hits=hits,
                    visitors=max(1, hits // 2),
                )
            )
        db.session.commit()

    def test_ranked_by_hits_with_sparkline(self, app, make_event):
        with app.app_context():
            popular = make_event(name="Big Show")
            quiet = make_event(name="Small Show")
            self._seed(popular.id, {0: 30, 1: 20, 2: 10})
            self._seed(quiet.id, {0: 1})
            result = traffic.events("30d")
        assert [r.name for r in result.rows] == ["Big Show", "Small Show"]
        assert result.rows[0].hits == 60
        assert result.rows[0].sparkline.strip()  # non-empty trend
        assert len(result.rows[0].sparkline) == result.spark_days

    def test_deleted_event_still_listed(self, app):
        with app.app_context():
            self._seed(4242, {0: 12})
            result = traffic.events("30d")
        assert result.rows[0].name == "(deleted event #4242)"
        assert result.rows[0].date_label == ""

    def test_timeframe_excludes_old_rows(self, app, make_event):
        with app.app_context():
            event = make_event()
            self._seed(event.id, {0: 5, 20: 100})
            week = traffic.events("7d")
            month = traffic.events("30d")
        assert week.rows[0].hits == 5
        assert month.rows[0].hits == 105

    def test_no_rows_raises(self, app):
        with app.app_context():
            with pytest.raises(AdminError, match="No recorded event views"):
                traffic.events("30d")
