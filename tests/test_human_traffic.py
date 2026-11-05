"""Tests for the admin Traffic panel's human counting (services/human_traffic.py)."""

import json
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from diytracker.services import analytics, human_traffic

DAY = date(2026, 10, 15)
SITE = "https://diytracker.ch/"
GOOGLE = "https://www.google.com/"
CHROME = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36"
)
IPHONE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 26_5 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/26.5 Mobile/15E148 Safari/604.1"
)


def _line(
    path="/",
    ip="1.2.3.4",
    referrer=GOOGLE,
    ua=CHROME,
    status=200,
    http="2.0",
    method="GET",
    when=None,
):
    when = when or datetime(2026, 10, 15, 12, tzinfo=UTC)
    stamp = when.strftime("%d/%b/%Y:%H:%M:%S %z")
    return (
        f'{ip} - - [{stamp}] "{method} {path} HTTP/{http}" {status} 1234 '
        f'"{referrer}" "{ua}"\n'
    )


@pytest.fixture
def count(app):
    adapter = app.url_map.bind("localhost")

    def run(*lines, first_day=DAY - timedelta(days=30)):
        counts, _ = human_traffic.collect_daily_human_counts(
            list(lines), first_day, adapter
        )
        return counts

    return run


class TestCountsPeople:
    def test_search_arrival_is_one_visitor(self, count):
        assert count(_line()) == {DAY: (1, 1)}

    def test_app_referrer_counts_as_arriving_from_elsewhere(self, count):
        line = _line(referrer="android-app://com.reddit.frontpage/")
        assert count(line) == {DAY: (1, 1)}

    def test_browsing_counts_every_page_view(self, count):
        lines = [
            _line("/", referrer="-"),
            _line("/map", referrer=SITE),
            _line("/about", referrer=SITE),
        ]
        assert count(*lines) == {DAY: (3, 1)}

    def test_conditional_get_counts(self, count):
        assert count(_line(status=304)) == {DAY: (1, 1)}

    def test_language_variants_are_page_views(self, count):
        assert count(_line("/fr/"), _line("/en/map")) == {DAY: (2, 1)}

    def test_old_ios_browser_without_safari_version(self, count):
        fxios = (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) AppleWebKit/605.1.15 "
            "(KHTML, like Gecko) FxiOS/157.1 Mobile/15E148 Safari/604.1"
        )
        assert count(_line(ua=fxios)) == {DAY: (1, 1)}

    def test_one_client_per_ip_and_ua(self, count):
        lines = [_line(), _line(ua=IPHONE), _line(ip="5.6.7.8")]
        assert count(*lines) == {DAY: (3, 3)}

    def test_only_pages_are_hits(self, count):
        lines = [
            _line(),
            _line("/calendar.ics"),
            _line("/robots.txt"),
            _line("/static/css/output.css"),
            _line("/no-such-page", status=404),
        ]
        assert count(*lines) == {DAY: (1, 1)}


class TestDropsBots:
    def test_declared_bot(self, count):
        ua = f"{CHROME} (compatible; GPTBot/1.4; +https://openai.com/gptbot)"
        assert count(_line(ua=ua)) == {}

    @pytest.mark.parametrize(
        "ua", ["-", "", "curl/8.7.1", "Go-http-client/1.1", "Mozilla/5.0"]
    )
    def test_not_a_browser(self, count, ua):
        assert count(_line(ua=ua)) == {}

    def test_single_page_without_referrer(self, count):
        assert count(_line(referrer="-")) == {}

    def test_single_page_faking_a_same_site_referrer(self, count):
        # The shape of the July proxy-pool floods: one page per client, each
        # claiming to come from the site itself.
        lines = [_line("/map", ip=f"10.0.0.{n}", referrer=SITE) for n in range(50)]
        assert count(*lines) == {}

    def test_http1(self, count):
        assert count(_line(http="1.1")) == {}

    def test_stale_browser_version(self, count):
        ua = CHROME.replace("Chrome/155", "Chrome/124")
        assert count(_line(ua=ua)) == {}

    def test_browser_age_is_judged_on_the_day_of_the_visit(self):
        ua = CHROME.replace("Chrome/155", "Chrome/124")
        assert human_traffic._is_recent_browser(ua, date(2024, 3, 1))
        assert not human_traffic._is_recent_browser(ua, DAY)

    def test_bare_ip_referrer_is_no_evidence(self, count):
        assert count(_line(referrer="http://147.79.101.4/")) == {}

    def test_too_many_pages(self, count):
        pages = human_traffic.MAX_PAGES_PER_DAY + 1
        assert count(*[_line() for _ in range(pages)]) == {}

    def test_scanner(self, count):
        lines = [
            _line(),
            _line("/.env", status=404),
            _line("/wp-login.php", status=404),
        ]
        assert count(*lines) == {}

    def test_one_stray_404_is_fine(self, count):
        lines = [_line(), _line("/.env", status=404)]
        assert count(*lines) == {DAY: (1, 1)}

    def test_crawling_every_language(self, count):
        assert count(_line("/"), _line("/fr/"), _line("/it/")) == {}

    def test_repeated_language_switching(self, count):
        switches = [_line("/set-language/fr", status=302) for _ in range(3)]
        assert count(_line(), *switches) == {}

    def test_rotating_user_agents_on_one_ip(self, count):
        uas = [CHROME.replace("Win64", f"Win{n}") for n in range(6)]
        assert count(*[_line(ua=ua) for ua in uas]) == {}


class TestExcludesAdmins:
    def test_admin_client_is_excluded_on_every_day(self, count):
        lines = [
            _line("/admin", when=datetime(2026, 10, 1, 12, tzinfo=UTC)),
            _line(),
        ]
        assert count(*lines) == {}

    def test_queue_counts_as_backstage(self, count):
        assert count(_line("/queue"), _line()) == {}

    def test_failed_admin_request_does_not_exclude(self, count):
        assert count(_line("/admin", status=403), _line()) == {DAY: (1, 1)}

    def test_same_ip_other_browser_still_counts(self, count):
        lines = [_line("/admin"), _line(ua=IPHONE)]
        assert count(*lines) == {DAY: (1, 1)}

    def test_session_before_the_window_still_identifies_the_admin(self, count):
        before = _line("/admin", when=datetime(2026, 8, 1, 12, tzinfo=UTC))
        assert count(before, _line(), first_day=date(2026, 10, 1)) == {}


class TestSwissDays:
    def test_late_utc_evening_belongs_to_next_swiss_day(self, count):
        line = _line(when=datetime(2026, 10, 14, 22, 30, tzinfo=UTC))
        assert count(line) == {DAY: (1, 1)}

    def test_days_before_the_window_are_dropped(self, count):
        line = _line(when=datetime(2026, 10, 1, 12, tzinfo=UTC))
        assert count(line, first_day=DAY) == {}


class TestGenerateTrafficStats:
    @pytest.fixture
    def stats_path(self, tmp_path, monkeypatch):
        path = tmp_path / "instance" / "analytics_stats.json"
        monkeypatch.setattr(analytics, "STATS_PATH", path)
        return path

    def _use_log(self, monkeypatch, tmp_path, text):
        log = tmp_path / "access.log"
        log.write_text(text)
        monkeypatch.setattr(human_traffic, "find_log_files", lambda: [log])

    def test_fails_without_logs(self, app, stats_path, monkeypatch):
        monkeypatch.setattr(human_traffic, "find_log_files", list)
        with app.app_context():
            success, message = human_traffic.generate_traffic_stats()
        assert success is False
        assert "No nginx access log files found" in message

    def test_keeps_cache_when_no_entries_in_window(
        self, app, stats_path, tmp_path, monkeypatch
    ):
        stats_path.parent.mkdir()
        stats_path.write_text('{"old": true}')
        self._use_log(
            monkeypatch, tmp_path, _line(when=datetime(2000, 1, 1, tzinfo=UTC))
        )
        with app.app_context():
            success, message = human_traffic.generate_traffic_stats()
        assert success is False
        assert "No log entries found" in message
        assert json.loads(stats_path.read_text()) == {"old": True}

    def test_writes_panel_stats(self, app, stats_path, tmp_path, monkeypatch):
        now = datetime.now(ZoneInfo("Europe/Zurich")).replace(hour=12)
        self._use_log(
            monkeypatch,
            tmp_path,
            _line(when=now, ua=IPHONE)
            + _line("/map", when=now, referrer=SITE, ua=IPHONE)
            + _line(when=now, ip="9.9.9.9", referrer="-", ua=IPHONE),
        )
        with app.app_context():
            success, message = human_traffic.generate_traffic_stats()
        assert success is True
        assert "Stats generated" in message
        stats = json.loads(stats_path.read_text())
        assert stats["totals"]["today"] == {"hits": 2, "visitors": 1}
        assert len(stats["daily"]) == 30

    def test_window_of_only_bots_writes_zeroes(
        self, app, stats_path, tmp_path, monkeypatch
    ):
        now = datetime.now(ZoneInfo("Europe/Zurich")).replace(hour=12)
        self._use_log(monkeypatch, tmp_path, _line(when=now, ua="curl/8.7.1"))
        with app.app_context():
            success, _ = human_traffic.generate_traffic_stats()
        assert success is True
        stats = json.loads(stats_path.read_text())
        assert stats["totals"]["today"] == {"hits": 0, "visitors": 0}


class TestAdminPanel:
    def test_scheduler_pass_reaches_the_admin_page(
        self, app, client, admin, login, tmp_path, monkeypatch
    ):
        from diytracker.services import event_views, page_views

        stats_path = tmp_path / "analytics_stats.json"
        monkeypatch.setattr(analytics, "STATS_PATH", stats_path)
        log = tmp_path / "access.log"
        now = datetime.now(ZoneInfo("Europe/Zurich")).replace(hour=12)
        # One person, plus a flood that the old panel counted in full.
        log.write_text(
            _line(when=now, ua=IPHONE)
            + "".join(
                _line("/map", ip=f"10.0.0.{n}", referrer=SITE, when=now, ua=IPHONE)
                for n in range(50)
            )
        )
        for module in (human_traffic, event_views, page_views):
            monkeypatch.setattr(module, "find_log_files", lambda: [log])
        monkeypatch.setattr(page_views, "PAGE_VIEWS_PATH", tmp_path / "page_views.json")

        analytics._generate_stats_guarded(app)

        login(admin)
        data = client.get("/admin/analytics/stats").get_json()
        assert data["totals"]["today"] == {"hits": 1, "visitors": 1}
        html = client.get("/admin").get_data(as_text=True)
        assert 'id="stat-visitors-today">1<' in html
        assert 'id="stat-hits-today">1<' in html
