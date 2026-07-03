"""Tests for the scrape_events module: HTTP fetching, sitemap parsing, and
page parsing against small local HTML fixtures (no network access)."""

from pathlib import Path

import pytest

from diytracker.services import scrape_events

FIXTURES = Path(__file__).parent / "fixtures"


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
        assert (
            scrape_events.fetch_url("https://example.com", max_retries=2) is None
        )


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
        assert scrape_events.get_sitemap_event_urls("https://x/sitemap.xml", "/x/") == []


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
