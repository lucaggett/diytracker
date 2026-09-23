"""Tests for the most-visited-pages service (services/page_views.py)."""

import json
from datetime import date, datetime

import pytest

from diytracker.services import page_views

UA = "Mozilla/5.0 (X11; Linux x86_64) Firefox/128.0"


def _line(path="/events/12/", ip="1.2.3.4", day=None, status=200, method="GET", ua=UA):
    day = day or datetime.now().date()
    stamp = day.strftime("%d/%b/%Y") + ":12:00:00 +0000"
    return (
        f'{ip} - - [{stamp}] "{method} {path} HTTP/1.1" {status} 1234 '
        f'"https://diytracker.ch/" "{ua}"'
    )


@pytest.fixture
def adapter(app):
    return app.url_map.bind("localhost")


@pytest.fixture
def cache_path(tmp_path, monkeypatch):
    path = tmp_path / "page_views.json"
    monkeypatch.setattr(page_views, "PAGE_VIEWS_PATH", path)
    return path


class TestCanonicalPagePath:
    def test_merges_language_prefixes(self, adapter):
        for path in ("/events/12/", "/fr/events/12/", "/en/events/12/"):
            assert page_views.canonical_page_path(path, adapter) == "/events/12/"
        assert page_views.canonical_page_path("/it/", adapter) == "/"
        assert page_views.canonical_page_path("/", adapter) == "/"

    def test_legal_pages_merge_onto_german(self, adapter):
        for lang in ("de", "fr", "it", "en"):
            path = f"/{lang}/impressum"
            assert page_views.canonical_page_path(path, adapter) == "/de/impressum"

    def test_drops_query_string(self, adapter):
        assert page_views.canonical_page_path("/fr/search?q=punk", adapter) == "/search"

    def test_ignores_non_pages(self, adapter):
        for path in (
            "/static/css/output.css",
            "/calendar.ics",
            "/events/12.ics",
            "/sitemap.xml",
            "/robots.txt",
            "/set-language/fr",
            "/events/archive/",  # scrape honeypot
            "/accessibility/secret-token",  # private credential URL
            "/queue",
            "/promoter/",
            "/login",
            "/wp-login.php",
            "/events/12",  # slash redirect
        ):
            assert page_views.canonical_page_path(path, adapter) is None, path


class TestCollect:
    def test_counts_hits_and_distinct_ips_across_languages(self, tmp_path, adapter):
        log = tmp_path / "access.log"
        log.write_text(
            "\n".join(
                [
                    _line(path="/events/12/", ip="1.1.1.1"),
                    _line(path="/fr/events/12/", ip="1.1.1.1"),
                    _line(path="/en/events/12/", ip="2.2.2.2"),
                    _line(path="/map", ip="3.3.3.3"),
                    _line(path="/map", ua="Googlebot/2.1"),
                    _line(path="/map", status=404),
                    _line(path="/map", method="POST"),
                ]
            )
            + "\n"
        )
        counts = page_views.collect_page_counts([log], None, adapter)
        assert counts == {"/events/12/": (3, 2), "/map": (1, 1)}

    def test_respects_valid_dates_window(self, tmp_path, adapter):
        log = tmp_path / "access.log"
        log.write_text(
            _line(path="/map", day=date(2026, 7, 9))
            + "\n"
            + _line(path="/about", day=date(2026, 7, 10))
            + "\n"
        )
        counts = page_views.collect_page_counts([log], {"10/Jul/2026"}, adapter)
        assert counts == {"/about": (1, 1)}


class TestGenerate:
    def test_fails_without_logs(self, app, cache_path, monkeypatch):
        monkeypatch.setattr(page_views, "find_log_files", list)
        success, _message = page_views.generate_page_view_stats()
        assert success is False
        assert page_views.read_page_views() is None

    def test_ranks_and_caches(self, app, tmp_path, cache_path, monkeypatch):
        log = tmp_path / "access.log"
        log.write_text(
            "\n".join(
                [_line(path="/map")]
                + [_line(path="/fr/", ip=f"9.9.9.{i}") for i in range(3)]
            )
            + "\n"
        )
        monkeypatch.setattr(page_views, "find_log_files", lambda: [log])
        success, _message = page_views.generate_page_view_stats()
        assert success is True
        stats = json.loads(cache_path.read_text())
        assert stats["pages"] == [
            {"path": "/", "hits": 3, "visitors": 3},
            {"path": "/map", "hits": 1, "visitors": 1},
        ]
        assert page_views.read_page_views() == stats
