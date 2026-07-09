"""Tests for the scrape_events module: HTTP fetching, sitemap parsing, and
page parsing against small local HTML fixtures (no network access)."""

from pathlib import Path

import pytest

from diytracker.services import scrape_events

FIXTURES = Path(__file__).parent / "fixtures"

# Captured before the autouse _no_robots stub below replaces it, so
# TestRobots can exercise the real implementation.
_REAL_ROBOTS_ALLOWED = scrape_events._robots_allowed


def _read_fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


class _FakeResponse:
    def __init__(self, status_code=200, text="", headers=None):
        self.status_code = status_code
        self.text = text
        self.apparent_encoding = "utf-8"
        self.headers = headers or {}


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    # fetch_url sleeps a random delay before every attempt; skip that in tests.
    monkeypatch.setattr(scrape_events.time, "sleep", lambda *_: None)


@pytest.fixture(autouse=True)
def _no_robots(monkeypatch):
    # fetch_url consults robots.txt (a real HTTP request) before every page;
    # allow everything by default so tests stay offline. Robots behaviour
    # itself is covered in TestRobots.
    monkeypatch.setattr(scrape_events, "_robots_allowed", lambda url: True)


class TestFetchUrl:
    def test_returns_text_on_200(self, monkeypatch):
        monkeypatch.setattr(
            scrape_events.requests,
            "get",
            lambda *a, **kw: _FakeResponse(200, "hello"),
        )
        assert scrape_events.fetch_url("https://example.com") == "hello"

    def test_returns_none_on_404(self, monkeypatch):
        monkeypatch.setattr(
            scrape_events.requests, "get", lambda *a, **kw: _FakeResponse(404)
        )
        assert scrape_events.fetch_url("https://example.com") is None

    def test_retries_on_429_then_succeeds(self, monkeypatch):
        responses = iter([_FakeResponse(429), _FakeResponse(200, "ok")])
        monkeypatch.setattr(
            scrape_events.requests, "get", lambda *a, **kw: next(responses)
        )
        assert scrape_events.fetch_url("https://example.com") == "ok"

    def test_gives_up_after_max_retries(self, monkeypatch):
        monkeypatch.setattr(
            scrape_events.requests, "get", lambda *a, **kw: _FakeResponse(503)
        )
        # 503 is a non-retried status: returns None on the first attempt.
        assert scrape_events.fetch_url("https://example.com") is None

    def test_returns_none_on_request_exception(self, monkeypatch):
        def boom(*a, **kw):
            raise scrape_events.requests.RequestException("network down")

        monkeypatch.setattr(scrape_events.requests, "get", boom)
        assert scrape_events.fetch_url("https://example.com", max_retries=2) is None

    def test_does_not_retry_on_403(self, monkeypatch):
        # A 403 means "go away"; retrying would only add load.
        calls = []

        def fake_get(*a, **kw):
            calls.append(a)
            return _FakeResponse(403)

        monkeypatch.setattr(scrape_events.requests, "get", fake_get)
        assert scrape_events.fetch_url("https://example.com") is None
        assert len(calls) == 1

    def test_skips_url_disallowed_by_robots(self, monkeypatch):
        def fail(*a, **kw):
            raise AssertionError("must not fetch a robots-disallowed URL")

        monkeypatch.setattr(scrape_events.requests, "get", fail)
        monkeypatch.setattr(scrape_events, "_robots_allowed", lambda url: False)
        assert scrape_events.fetch_url("https://example.com/private") is None

    def test_sends_honest_user_agent(self, monkeypatch):
        seen = {}

        def fake_get(url, headers=None, timeout=None):
            seen["ua"] = (headers or {}).get("User-Agent", "")
            return _FakeResponse(200, "ok")

        monkeypatch.setattr(scrape_events.requests, "get", fake_get)
        scrape_events.fetch_url("https://example.com")
        assert seen["ua"].startswith("diytracker")


class TestRobots:
    @pytest.fixture(autouse=True)
    def _real_robots(self, monkeypatch):
        # Undo the module-wide _no_robots stub and start with a cold cache.
        monkeypatch.setattr(scrape_events, "_robots_allowed", _REAL_ROBOTS_ALLOWED)
        scrape_events._robots_cache.clear()
        yield
        scrape_events._robots_cache.clear()

    def test_disallowed_path_blocked(self, monkeypatch):
        robots = "User-agent: *\nDisallow: /private/\n"
        monkeypatch.setattr(
            scrape_events.requests,
            "get",
            lambda *a, **kw: _FakeResponse(200, robots),
        )
        assert scrape_events._robots_allowed("https://example.com/private/x") is False
        assert scrape_events._robots_allowed("https://example.com/public/x") is True

    def test_missing_robots_allows_all(self, monkeypatch):
        monkeypatch.setattr(
            scrape_events.requests, "get", lambda *a, **kw: _FakeResponse(404)
        )
        assert scrape_events._robots_allowed("https://example.com/anything") is True

    def test_unreachable_robots_allows_all(self, monkeypatch):
        def boom(*a, **kw):
            raise scrape_events.requests.RequestException("network down")

        monkeypatch.setattr(scrape_events.requests, "get", boom)
        assert scrape_events._robots_allowed("https://example.com/anything") is True

    def test_parser_is_cached_per_host(self, monkeypatch):
        calls = []

        def fake_get(url, **kw):
            calls.append(url)
            return _FakeResponse(200, "User-agent: *\nDisallow:\n")

        monkeypatch.setattr(scrape_events.requests, "get", fake_get)
        scrape_events._robots_allowed("https://example.com/a")
        scrape_events._robots_allowed("https://example.com/b")
        assert calls == ["https://example.com/robots.txt"]


class TestGetSitemapEventUrls:
    def test_extracts_matching_urls(self, monkeypatch):
        sitemap_xml = """<?xml version="1.0"?>
        <urlset>
          <url><loc>https://metalgigs.ch/konzerte/a</loc></url>
          <url><loc>https://metalgigs.ch/festivals/b</loc></url>
          <url><loc>https://metalgigs.ch/konzerte/a</loc></url>
        </urlset>"""
        monkeypatch.setattr(scrape_events, "fetch_url", lambda url, **kw: sitemap_xml)
        urls = scrape_events.get_sitemap_event_urls(
            "https://metalgigs.ch/sitemap.xml", "/konzerte/"
        )
        assert urls == ["https://metalgigs.ch/konzerte/a"]

    def test_follows_sitemap_index(self, monkeypatch):
        index_xml = """<?xml version="1.0"?>
        <sitemapindex>
          <sitemap><loc>https://metalgigs.ch/sitemap-1.xml</loc></sitemap>
          <sitemap><loc>https://metalgigs.ch/sitemap-2.xml</loc></sitemap>
        </sitemapindex>"""
        sub_1 = "<urlset><url><loc>https://metalgigs.ch/konzerte/a</loc></url></urlset>"
        sub_2 = "<urlset><url><loc>https://metalgigs.ch/konzerte/b</loc></url></urlset>"
        pages = {
            "https://metalgigs.ch/sitemap.xml": index_xml,
            "https://metalgigs.ch/sitemap-1.xml": sub_1,
            "https://metalgigs.ch/sitemap-2.xml": sub_2,
        }
        monkeypatch.setattr(
            scrape_events, "fetch_url", lambda url, **kw: pages.get(url)
        )
        urls = scrape_events.get_sitemap_event_urls(
            "https://metalgigs.ch/sitemap.xml", "/konzerte/"
        )
        assert urls == [
            "https://metalgigs.ch/konzerte/a",
            "https://metalgigs.ch/konzerte/b",
        ]

    def test_returns_empty_when_fetch_fails(self, monkeypatch):
        monkeypatch.setattr(scrape_events, "fetch_url", lambda url, **kw: None)
        assert (
            scrape_events.get_sitemap_event_urls("https://x/sitemap.xml", "/x/") == []
        )

    def test_hints_skip_irrelevant_sub_sitemaps(self, monkeypatch):
        index_xml = """<sitemapindex>
          <sitemap><loc>https://metalgigs.ch/sitemap_concerts.xml</loc></sitemap>
          <sitemap><loc>https://metalgigs.ch/sitemap_bands.xml</loc></sitemap>
        </sitemapindex>"""
        fetched = []

        def fake_fetch(url, **kw):
            fetched.append(url)
            if url.endswith("sitemap.xml"):
                return index_xml
            return (
                "<urlset><url><loc>https://metalgigs.ch/konzerte/a</loc></url></urlset>"
            )

        monkeypatch.setattr(scrape_events, "fetch_url", fake_fetch)
        urls = scrape_events.get_sitemap_event_urls(
            "https://metalgigs.ch/sitemap.xml",
            "/konzerte/",
            sub_sitemap_hints=["concert"],
        )
        assert urls == ["https://metalgigs.ch/konzerte/a"]
        assert "https://metalgigs.ch/sitemap_bands.xml" not in fetched

    def test_hints_fall_back_to_all_when_nothing_matches(self, monkeypatch):
        index_xml = """<sitemapindex>
          <sitemap><loc>https://metalgigs.ch/sitemap-renamed.xml</loc></sitemap>
        </sitemapindex>"""
        pages = {
            "https://metalgigs.ch/sitemap.xml": index_xml,
            "https://metalgigs.ch/sitemap-renamed.xml": (
                "<urlset><url><loc>https://metalgigs.ch/konzerte/a</loc></url></urlset>"
            ),
        }
        monkeypatch.setattr(
            scrape_events, "fetch_url", lambda url, **kw: pages.get(url)
        )
        urls = scrape_events.get_sitemap_event_urls(
            "https://metalgigs.ch/sitemap.xml",
            "/konzerte/",
            sub_sitemap_hints=["concert"],
        )
        assert urls == ["https://metalgigs.ch/konzerte/a"]


class TestGetPetziEventUrls:
    def test_extracts_absolute_and_relative_links(self, monkeypatch):
        home_html = """
        <html><body>
          <a href="https://www.petzi.ch/en/events/one">One</a>
          <a href="/en/events/two">Two</a>
          <a href="/en/events/one">One again</a>
          <a href="/en/about">Not an event</a>
        </body></html>
        """
        monkeypatch.setattr(scrape_events, "fetch_url", lambda url, **kw: home_html)
        urls = scrape_events.get_petzi_event_urls()
        assert urls == [
            "https://www.petzi.ch/en/events/one",
            "https://www.petzi.ch/en/events/two",
        ]

    def test_returns_empty_when_home_page_unavailable(self, monkeypatch):
        monkeypatch.setattr(scrape_events, "fetch_url", lambda url, **kw: None)
        assert scrape_events.get_petzi_event_urls() == []


class TestParseMetalgigsEvent:
    """Parses a small local test page standing in for a real metalgigs.ch
    event page, so the JSON-LD + HTML-fallback logic runs without network."""

    def test_returns_none_when_page_unfetchable(self, monkeypatch):
        monkeypatch.setattr(scrape_events, "fetch_url", lambda url, **kw: None)
        assert scrape_events.parse_metalgigs_event("https://metalgigs.ch/x") is None

    def test_parses_json_ld_fields(self, monkeypatch):
        html = _read_fixture("metalgigs_event.html")
        monkeypatch.setattr(scrape_events, "fetch_url", lambda url, **kw: html)
        event = scrape_events.parse_metalgigs_event(
            "https://metalgigs.ch/konzerte/doom-night"
        )
        assert event["source"] == "metalgigs"
        assert event["title"] == "Doom Night: Ashen Crown"
        assert event["start_date"] == "2026-11-05"
        assert event["end_date"] == "2026-11-05"
        assert event["doors_open"] == "19:00"
        assert event["venue_name"] == "Exil"
        assert event["street_address"] == "Hardstrasse 245"
        assert event["city"] == "Zürich"
        assert event["postal_code"] == "8005"
        assert event["ticket_price"] == "39.80"
        assert event["ticket_currency"] == "CHF"
        assert event["performers"] == "Ashen Crown, Grey Cairn"
        assert event["region"] == "ZH"

    def test_falls_back_to_html_when_no_json_ld(self, monkeypatch):
        html = _read_fixture("metalgigs_event_no_jsonld.html")
        monkeypatch.setattr(scrape_events, "fetch_url", lambda url, **kw: html)
        event = scrape_events.parse_metalgigs_event(
            "https://metalgigs.ch/konzerte/crust-fest"
        )
        assert event["performers"] == "Crust Fest: Rusted Chains"  # from <h1>
        assert event["styles"] == "Crust Punk, D-Beat"
        assert event["start_date"] == "2026-12-12"
        assert event["end_date"] == "2026-12-12"
        assert event["doors_open"] == "20:00"
        assert event["start_time"] == "20:30"
        assert event["venue_name"] == "Kraftwerk"
        assert event["street_address"] == "Industriestrasse 1"
        assert event["city"] == "Bern"
        assert event["postal_code"] == "3000"
        assert event["ticket_price"] == "25.00"
        assert event["region"] == "BE"


class TestParsePetziEvent:
    def test_returns_none_when_page_unfetchable(self, monkeypatch):
        monkeypatch.setattr(scrape_events, "fetch_url", lambda url, **kw: None)
        assert scrape_events.parse_petzi_event("https://petzi.ch/en/events/x") is None

    def test_parses_test_page(self, monkeypatch):
        html = _read_fixture("petzi_event.html")
        monkeypatch.setattr(scrape_events, "fetch_url", lambda url, **kw: html)
        event = scrape_events.parse_petzi_event(
            "https://www.petzi.ch/en/events/hardcore-matinee"
        )
        assert event["source"] == "petzi"
        assert event["title"] == "Hardcore Matinee"
        assert event["start_date"] == "2026-11-06"
        assert event["end_date"] == "2026-11-06"
        assert event["ticket_price"] == "20.00"
        assert event["ticket_currency"] == "CHF"
        assert event["venue_name"] == "Kasheme"
        assert event["city"] == "Zürich"
        assert event["doors_open"] == "18:00"
        assert event["start_time"] == "18:30"
        assert event["organizer"] == "Kasheme Crew"
        assert event["styles"] == "Hardcore, Concert"
        assert "matinee show" in event["description"]
        assert event["region"] == "ZH"


class TestWriteCsv:
    def test_writes_header_and_rows_with_preferred_column_order(self, tmp_path):
        events = [
            {"source": "metalgigs", "url": "https://x/1", "title": "A", "extra": "z"},
            {"source": "petzi", "url": "https://x/2", "title": "B"},
        ]
        out_file = tmp_path / "events.csv"
        scrape_events.write_csv(events, str(out_file))

        content = out_file.read_text(encoding="utf-8")
        header = content.splitlines()[0].split(",")
        assert header[:3] == ["source", "url", "title"]
        assert "extra" in header  # unknown fields appended at the end
        assert "A" in content and "B" in content

    def test_writes_empty_file_for_no_events(self, tmp_path):
        out_file = tmp_path / "events.csv"
        scrape_events.write_csv([], str(out_file))
        content = out_file.read_text(encoding="utf-8")
        assert content.splitlines()[0].startswith("source,url,title")
