"""SEO features: slug/price/acts helpers, JSON-LD builders, canonical tags,
city landing pages, past-event notices, and the sitemap upgrades."""

import json
import re
import xml.etree.ElementTree as ET

import pytest

from diytracker.models import VenueAccessibility, db, utcnow
from diytracker.services.seo import parse_acts, parse_price, slugify


def extract_json_ld(html, ld_type):
    """Parse every JSON-LD block on the page; return the one of ld_type."""
    blocks = re.findall(
        r'<script type="application/ld\+json">(.*?)</script>', html, re.DOTALL
    )
    assert blocks, "no JSON-LD script tag found"
    for block in blocks:
        data = json.loads(block)
        if data.get("@type") == ld_type:
            return data
    raise AssertionError(f"no JSON-LD block of type {ld_type}")


class TestSlugify:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Zürich", "zuerich"),
            ("Genève 8", "geneve-8"),
            ("GENÈVE", "geneve"),
            ("Bienne-Biel", "bienne-biel"),
            ("Wiedlisbach / Wangen a.A.", "wiedlisbach-wangen-a-a"),
            ("  Fribourg ", "fribourg"),
            ("", ""),
        ],
    )
    def test_swiss_slugs(self, raw, expected):
        assert slugify(raw) == expected


class TestParsePrice:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("13.00", 13.0),
            ("46", 46.0),
            ("8-25", 8.0),
            ("31.30.-", 31.3),
            ("15,50", 15.5),
            ("Kollekte", 0.0),
            ("gratis", 0.0),
            ("free entry", 0.0),
            ("00.00", 0.0),
            ("", None),
            ("ask at the door", None),
        ],
    )
    def test_parse_price(self, raw, expected):
        assert parse_price(raw) == expected


class TestParseActs:
    def test_newline_separated(self):
        assert parse_acts("KANONENFIEBER\nAUGN (DE)") == [
            "KANONENFIEBER",
            "AUGN (DE)",
        ]

    def test_pipe_and_comma_with_duplicates(self):
        assert parse_acts("Band A | Band B, band a") == ["Band A", "Band B"]

    def test_empty(self):
        assert parse_acts(None) == []
        assert parse_acts("  \n ") == []


class TestEventJsonLd:
    def test_required_fields_and_timezone(self, client, make_event):
        ev = make_event(name="Doom Night")
        html = client.get(f"/events/{ev.id}/").data.decode()
        ld = extract_json_ld(html, "MusicEvent")
        assert ld["name"] == "Doom Night"
        assert ld["location"]["name"] == "Kasheme"
        assert ld["location"]["address"]["addressCountry"] == "CH"
        # Europe/Zurich offset, never a naive timestamp.
        assert re.search(r"\+0[12]:00$", ld["startDate"])
        assert ld["eventStatus"] == "https://schema.org/EventScheduled"
        assert ld["performer"] == [
            {"@type": "MusicGroup", "name": "Band A"},
            {"@type": "MusicGroup", "name": "Band B"},
        ]
        assert ld["offers"]["price"] == 20
        assert ld["offers"]["priceCurrency"] == "CHF"

    def test_cancelled_status(self, client, make_event):
        ev = make_event(status="cancelled")
        html = client.get(f"/events/{ev.id}/").data.decode()
        ld = extract_json_ld(html, "MusicEvent")
        assert ld["eventStatus"] == "https://schema.org/EventCancelled"
        assert "This event has been cancelled." in html

    def test_donation_price_is_zero(self, client, make_event):
        ev = make_event(ticket_price="Kollekte")
        html = client.get(f"/events/{ev.id}/").data.decode()
        ld = extract_json_ld(html, "MusicEvent")
        assert ld["offers"]["price"] == 0

    def test_unparseable_price_omits_offers(self, client, make_event):
        ev = make_event(ticket_price="ask at the door")
        html = client.get(f"/events/{ev.id}/").data.decode()
        ld = extract_json_ld(html, "MusicEvent")
        assert "offers" not in ld


class TestVenueJsonLd:
    def test_venue_json_ld_parses(self, client, make_venue):
        venue = make_venue()
        html = client.get(f"/venues/{venue.id}/").data.decode()
        ld = extract_json_ld(html, "MusicVenue")
        assert ld["name"] == "Kasheme"
        assert ld["address"]["addressLocality"] == "Zürich"

    def test_amenity_features_from_accessibility(self, client, make_venue):
        venue = make_venue()
        db.session.add(
            VenueAccessibility(
                venue_id=venue.id,
                step_free_entrance="yes",
                accessible_toilet="partial",
                hearing_loop="no",
                updated_at=utcnow(),
            )
        )
        db.session.commit()
        html = client.get(f"/venues/{venue.id}/").data.decode()
        ld = extract_json_ld(html, "MusicVenue")
        features = {f["name"]: f["value"] for f in ld["amenityFeature"]}
        assert features["Step-free entrance"] is True
        assert features["Wheelchair accessible toilet"] is False  # partial
        assert "Hearing loop" not in features  # "no" is not advertised


class TestWebsiteJsonLd:
    def test_homepage_has_website_json_ld(self, client):
        html = client.get("/").data.decode()
        ld = extract_json_ld(html, "WebSite")
        assert ld["url"] == "http://localhost/"


class TestCanonical:
    def test_canonical_on_pages(self, client, make_event):
        ev = make_event()
        for path in ("/", f"/events/{ev.id}/", f"/venues/{ev.venue_id}/"):
            html = client.get(path).data.decode()
            assert f'<link rel="canonical" href="http://localhost{path}">' in html
            assert f'property="og:url" content="http://localhost{path}"' in html

    def test_canonical_host_config(self, app, client, make_event):
        ev = make_event()
        app.config["CANONICAL_HOST"] = "https://diytracker.ch"
        try:
            html = client.get(f"/events/{ev.id}/").data.decode()
            assert (
                '<link rel="canonical" '
                f'href="https://diytracker.ch/events/{ev.id}/">' in html
            )
        finally:
            app.config["CANONICAL_HOST"] = ""


class TestCityPages:
    def test_city_page_renders_events(self, client, make_event):
        ev = make_event(name="Doom Night")
        resp = client.get("/zuerich/")
        html = resp.data.decode()
        assert resp.status_code == 200
        assert "<h1" in html and "Zürich" in html
        assert f'href="/events/{ev.id}/"' in html
        assert f'href="/venues/{ev.venue_id}/"' in html

    def test_unknown_city_404(self, client, make_event):
        make_event()
        assert client.get("/atlantis/").status_code == 404

    def test_reserved_slug_not_a_city(self, client):
        # Routing keeps reserved segments away from the city route entirely.
        assert client.get("/logout").status_code == 405

    def test_city_spellings_merge_by_slug(self, client, make_venue, make_event):
        make_event(venue=make_venue(name="Cave", city="GENÈVE"))
        make_event(venue=make_venue(name="Usine", city="Genève"))
        resp = client.get("/geneve/")
        html = resp.data.decode()
        assert resp.status_code == 200
        assert "Cave" in html and "Usine" in html

    def test_city_without_upcoming_events_404s(self, client, make_venue, make_event):
        make_event(venue=make_venue(city="Basel"), days_from_now=-30)
        assert client.get("/basel/").status_code == 404

    def test_footer_links_city_pages(self, client, make_event):
        make_event()
        assert 'href="/zuerich/"' in client.get("/").data.decode()


class TestPastEventNotice:
    def test_past_event_stays_live_with_notice(self, client, make_event):
        venue_event = make_event(days_from_now=-5)
        upcoming = make_event(
            name="Next Show", venue=venue_event.venue, days_from_now=5
        )
        resp = client.get(f"/events/{venue_event.id}/")
        html = resp.data.decode()
        assert resp.status_code == 200
        assert "This event has already taken place." in html
        assert f'href="/events/{upcoming.id}/"' in html

    def test_upcoming_event_has_no_notice(self, client, make_event):
        ev = make_event(days_from_now=5)
        html = client.get(f"/events/{ev.id}/").data.decode()
        assert "This event has already taken place." not in html


class TestSitemapSeo:
    def test_sitemap_valid_xml_with_lastmod_and_cities(self, client, make_event):
        ev = make_event()
        resp = client.get("/sitemap.xml")
        root = ET.fromstring(resp.data)
        ns = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
        locs = [url.findtext(f"{ns}loc") for url in root.findall(f"{ns}url")]
        assert f"http://localhost/events/{ev.id}/" in locs
        assert "http://localhost/zuerich/" in locs
        event_url = next(
            url
            for url in root.findall(f"{ns}url")
            if url.findtext(f"{ns}loc") == f"http://localhost/events/{ev.id}/"
        )
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", event_url.findtext(f"{ns}lastmod"))

    def test_sitemap_drops_events_older_than_two_years(self, client, make_event):
        old = make_event(days_from_now=-800)
        recent = make_event(name="Recent", days_from_now=-30)
        data = client.get("/sitemap.xml").data
        assert f"/events/{recent.id}/".encode() in data
        assert f"/events/{old.id}/".encode() not in data


class TestCrawlerSafety:
    def test_sitemap_and_robots_never_scored(self, client, app):
        from diytracker.models import ScrapeSuspect
        from diytracker.services.scrape_detection import detector

        detector.enabled = True
        detector.reset()
        try:
            # No browser headers, high rate — would normally accumulate score.
            for _ in range(60):
                client.get("/sitemap.xml")
                client.get("/robots.txt")
            assert ScrapeSuspect.query.count() == 0
        finally:
            detector.enabled = False
            detector.reset()
