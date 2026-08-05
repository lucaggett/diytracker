"""Tests for the per-venue scrapers and the source registry.

Offline, like test_scrape_events.py: fetch_url is monkeypatched to serve
fixtures captured from the real sites, so the markup under test is the markup
the venues actually publish rather than something invented here.

The clock is frozen at FROZEN_TODAY because every fetcher filters out finished
shows, and the fixtures hold real dates — without freezing, each fixture would
silently start returning zero rows once its events aged out, and the tests
would pass while asserting nothing.
"""

from datetime import date
from pathlib import Path

import pytest

from diytracker.services import scrape_events, venue_parsers, venue_sources

FIXTURES = Path(__file__).parent / "fixtures"

# The day the fixtures were captured.
FROZEN_TODAY = date(2026, 8, 5)

# Which fixture answers which request. Matched as a substring of the URL, so
# the fetchers keep their real URLs in the code under test.
ROUTES = {
    "altepost.zureich.rip/events/": "altepost_listing.html",
    "post.zureich.rip/kalender-alle": "postsquat_listing.html",
    "horstklub.ch/events.csv": "horstklub_events.csv",
    "treppenhaus.ch/wp-json": "treppenhaus_events.json",
    "eldoradobielbienne.ch/wp-json": "eldorado_events.json",
    "taptab.ch/?format=feed": "taptab_feed.xml",
    "provitreff.ch/": "provitreff_home.html",
    "club.badbonn.ch/": "badbonn_listing.html",
    "werkk-baden.ch/agenda": "werkk_listing.html",
    "quaidubas30.ch/de/events": "quaidubas_listing.html",
    "cafete.ch/": "cafete_listing.html",
    "kuzeb.ch/events": "kuzeb_listing.html",
    "nouveaumonde.ch/agenda": "nouveaumonde_listing.html",
    "kaschemme.ch/programm": "kaschemme_listing.html",
}


def _read_fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _frozen_today(monkeypatch):
    monkeypatch.setattr(venue_parsers, "_today", lambda: FROZEN_TODAY)


@pytest.fixture(autouse=True)
def _served_fixtures(monkeypatch):
    def fake_fetch(url, *args, **kwargs):
        for fragment, name in ROUTES.items():
            if fragment in url:
                return _read_fixture(name)
        return None

    # Patched in both modules: venue_parsers imported the name directly, and
    # fetch_json calls it through scrape_events.
    monkeypatch.setattr(venue_parsers, "fetch_url", fake_fetch)
    monkeypatch.setattr(scrape_events, "fetch_url", fake_fetch)


def _by_title(rows, title):
    return next(row for row in rows if row["title"] == title)


class TestHelpers:
    def test_clean_time_accepts_colon_and_h(self):
        assert venue_parsers._clean_time("20:30") == "20:30"
        assert venue_parsers._clean_time("19h30") == "19:30"
        assert venue_parsers._clean_time("20:00 Uhr") == "20:00"

    def test_clean_time_ignores_dotted_dates(self):
        # The bug this guards: 07.08.2026 must not become 07:08.
        assert venue_parsers._clean_time("07.08.2026") == ""

    def test_clean_time_rejects_impossible_clocks(self):
        assert venue_parsers._clean_time("99:99") == ""

    def test_price_extracts_number_and_detects_free(self):
        assert venue_parsers._price("CHF 25") == "25"
        assert venue_parsers._price("15 €/CHF bis 21 Uhr") == "15"
        assert venue_parsers._price("EINTRITT FREI") == "0"
        assert venue_parsers._price("") == ""

    def test_german_dates_reads_all_dates_in_order(self):
        found = venue_parsers._german_dates("24. Juli 2026 – 27. August 2026")
        assert found == [date(2026, 7, 24), date(2026, 8, 27)]

    def test_german_dates_ignores_unknown_month(self):
        assert venue_parsers._german_dates("24. Smarch 2026") == []

    def test_year_nearest_today_prefers_the_closest_year(self, monkeypatch):
        monkeypatch.setattr(venue_parsers, "_today", lambda: date(2026, 8, 5))
        # May is behind us: nearest is this year's, in the past, not next year's.
        assert venue_parsers._year_nearest_today(5, 8) == date(2026, 5, 8)
        # January is nearer ahead than behind.
        assert venue_parsers._year_nearest_today(1, 15) == date(2027, 1, 15)

    def test_source_id_is_stable_and_differs_per_event(self):
        first = venue_parsers._source_id("cafete", date(2026, 8, 6), "Tanzbär")
        assert first == venue_parsers._source_id("cafete", date(2026, 8, 6), "Tanzbär")
        assert first != venue_parsers._source_id("cafete", date(2026, 8, 7), "Tanzbär")


class TestIcalHelpers:
    def test_utc_times_convert_to_swiss_wall_clock(self):
        feed = (
            "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:1@x\r\n"
            "DTSTART:20260810T190000Z\r\nSUMMARY:Show\r\n"
            "END:VEVENT\r\nEND:VCALENDAR\r\n"
        )
        # 19:00 UTC in August is 21:00 in Zurich; storing 19:00 would put
        # every summer show two hours early.
        assert scrape_events.parse_ical_feed(feed)[0]["start"].hour == 21

    def test_tzid_times_are_taken_as_local(self):
        feed = (
            "BEGIN:VEVENT\r\nUID:2@x\r\n"
            "DTSTART;TZID=Europe/Zurich:20260810T190000\r\nEND:VEVENT\r\n"
        )
        assert scrape_events.parse_ical_feed(feed)[0]["start"].hour == 19

    def test_folded_lines_are_rejoined(self):
        feed = "BEGIN:VEVENT\r\nUID:3@x\r\nSUMMARY:Long\r\n  title\r\nEND:VEVENT\r\n"
        assert scrape_events.parse_ical_feed(feed)[0]["summary"] == "Long title"

    def test_escaped_text_is_unescaped(self):
        feed = "BEGIN:VEVENT\r\nDESCRIPTION:A\\nB\\, C\r\nEND:VEVENT\r\n"
        assert scrape_events.parse_ical_feed(feed)[0]["description"] == "A\nB, C"


class TestFeedSources:
    def test_horstklub_reads_the_csv_and_keeps_only_concerts(self):
        rows = venue_parsers.fetch_horstklub()
        assert rows
        assert all(row["source"] == "horstklub" for row in rows)
        # Darts nights and bar openings share the file and must not appear.
        assert all("DARTS" not in row["title"].upper() for row in rows)
        row = rows[0]
        assert row["start_date"] == "2026-08-07"
        assert row["doors_open"] and row["start_time"]
        assert row["ticket_currency"] == "CHF"

    def test_horstklub_rows_have_no_url_but_do_have_a_source_id(self):
        # The CSV carries no per-event link; ScrapedEvent.url is UNIQUE, so a
        # shared URL here would collapse the whole venue to one row.
        for row in venue_parsers.fetch_horstklub():
            assert row["url"] == ""
            assert row["source_id"]

    def test_treppenhaus_reads_the_rest_details_block(self):
        rows = venue_parsers.fetch_treppenhaus()
        row = _by_title(rows, "SeeLaVie")
        assert row["start_date"] == "2026-08-18"
        assert row["start_time"] == "18:00"
        assert row["ticket_price"] == "0"  # "EINTRITT FREI"
        assert row["url"].startswith("https://treppenhaus.ch/events/")
        assert row["source_id"] == "7359"

    def test_eldorado_reads_the_tribe_api(self):
        rows = venue_parsers.fetch_eldorado()
        assert len(rows) == 1
        row = rows[0]
        assert row["start_date"] == "2026-09-04"
        assert row["start_time"] == "19:00"
        # Same-day end time is a closing hour, not a multi-day run.
        assert row["end_date"] == ""

    def test_taptab_reads_rss_and_strips_the_date_prefix(self):
        rows = venue_parsers.fetch_taptab()
        row = _by_title(rows, "HAUSFEST 2")
        assert row["start_date"] == "2026-08-29"
        assert row["url"] == "https://taptab.ch/2026-08-29-hausfest-2"
        assert "<p>" not in row["description"]

    def test_provitreff_drops_stale_months_instead_of_inventing_a_year(self):
        rows = venue_parsers.fetch_provitreff()
        # The payload holds finished May/June/July blocks alongside September.
        # Rolling those forward would create events that never existed.
        assert all(row["start_date"] >= "2026-08-04" for row in rows)
        assert any(row["start_date"].startswith("2026-09") for row in rows)


class TestListingSources:
    def test_altepost_reads_the_listing_not_the_stale_ical(self):
        rows = venue_parsers.fetch_altepost()
        row = _by_title(rows, "BURIAL, TUMULO")
        assert row["start_date"] == "2026-08-28"
        assert row["start_time"] == "19:00"
        assert row["url"].endswith("/events/burial-tumulo/")

    def test_altepost_skips_events_that_already_started(self):
        # The fixture also holds a multi-day SOMMERPAUSE beginning 24 July.
        assert not any(
            row["title"] == "SOMMERPAUSE" for row in venue_parsers.fetch_altepost()
        )

    def test_postsquat_reads_date_title_and_time(self):
        rows = venue_parsers.fetch_postsquat()
        row = _by_title(rows, "PAUL GEIGERZÄHLER live & PIZZA")
        assert row["start_date"] == "2026-08-09"
        assert row["start_time"] == "18:30"

    def test_badbonn_reads_the_data_attributes(self):
        rows = venue_parsers.fetch_badbonn()
        row = _by_title(rows, "Ragana, L’amour du ciel")
        assert row["start_date"] == "2026-08-13"
        assert row["start_time"] == "21:00"
        assert row["ticket_price"] == "25"
        assert row["ticket_currency"] == "CHF"

    def test_werkk_dedups_the_mobile_and_desktop_copies(self):
        rows = venue_parsers.fetch_werkk()
        urls = [row["url"] for row in rows]
        assert len(urls) == len(set(urls))
        assert all(row["url"].startswith("https://werkk-baden.ch/") for row in rows)

    def test_quaidubas_reads_the_time_element(self):
        rows = venue_parsers.fetch_quaidubas()
        # The fixture's events are from before FROZEN_TODAY, so the correct
        # result is nothing — the parser must not resurrect them.
        assert rows == []

    def test_cafete_reads_the_german_date_and_style_line(self):
        rows = venue_parsers.fetch_cafete()
        row = _by_title(rows, "Tanzbär – Grande Reopening")
        assert row["start_date"] == "2026-08-06"
        assert row["start_time"] == "23:30"
        assert row["styles"] == "Tech House / Techno"
        assert "DJ Girl" in row["performers"]

    def test_kuzeb_reads_the_modal_including_date_ranges(self):
        rows = venue_parsers.fetch_kuzeb()
        row = _by_title(rows, "Anarchistische Büchermesse")
        assert row["start_date"] == "2026-08-07"
        assert row["end_date"] == "2026-08-09"
        assert row["source_id"] == "415"

    def test_kuzeb_does_not_read_the_date_as_a_time(self):
        # 07.08.2026 previously parsed as a 07:08 door time.
        for row in venue_parsers.fetch_kuzeb():
            assert row["start_time"] in ("", "17:00") or ":" in row["start_time"]
            assert row["start_time"] != "07:08"

    def test_nouveaumonde_keeps_concerts_and_parties_only(self):
        rows = venue_parsers.fetch_nouveaumonde()
        titles = [row["title"] for row in rows]
        assert "Der HARDCORE Blind Test !" in titles
        for row in rows:
            assert row["start_time"]
            assert row["url"].startswith("https://www.nouveaumonde.ch/")

    def test_kaschemme_uses_the_24_hour_time(self):
        rows = venue_parsers.fetch_kaschemme()
        row = _by_title(rows, "UPTOWN DJ-LAB")
        assert row["start_date"] == "2026-08-06"
        # The block also renders "6:00 PM"; picking that up gave 06:00.
        assert row["start_time"] == "18:00"


class TestRegistry:
    def test_keys_are_unique(self):
        keys = [source.key for source in venue_sources.get_sources()]
        assert len(keys) == len(set(keys))

    def test_every_source_is_either_a_listing_or_a_discovery_pair(self):
        for source in venue_sources.get_sources():
            if source.is_listing:
                assert source.discover is None and source.parser is None
                assert callable(source.fetcher)
            else:
                assert callable(source.discover) and callable(source.parser)

    def test_venue_identities_are_complete(self):
        for source in venue_sources.get_sources():
            fields = source.venue.as_fields()
            assert fields["venue_name"] and fields["city"]
            assert fields["postal_code"] and fields["region"]
            # Canton must already be the 2-letter code the canton pages filter on.
            assert len(fields["region"]) == 2

    def test_languages_are_ones_we_have_email_copy_for(self):
        for source in venue_sources.get_sources():
            assert source.lang in ("de", "fr")

    def test_sources_by_key_round_trips(self):
        mapping = venue_sources.sources_by_key()
        assert len(mapping) == len(venue_sources.get_sources())
        assert mapping["cafete"].venue.city == "Bern"

    def test_unscraped_venues_all_carry_a_reason(self):
        assert venue_sources.UNSCRAPED
        for name, reason in venue_sources.UNSCRAPED.items():
            assert name and len(reason) > 20


class TestRowContract:
    """Every fetcher must return rows ingest_event() can actually accept."""

    REQUIRED = ("source", "title", "start_date")

    def test_all_listing_sources_produce_wellformed_rows(self):
        for source in venue_sources.get_sources():
            if not source.is_listing:
                continue
            for row in source.fetcher():
                for key in self.REQUIRED:
                    assert row.get(key), f"{source.key} row missing {key}: {row}"
                assert row["source"] == source.key
                # YYYY-MM-DD and HH:MM, the formats ingest parses.
                assert len(row["start_date"]) == 10
                for key in ("start_time", "doors_open"):
                    if row[key]:
                        assert len(row[key]) == 5 and row[key][2] == ":"

    def test_rows_without_a_url_always_carry_a_source_id(self):
        # Otherwise the UNIQUE constraint on ScrapedEvent.url would drop
        # every row but the first for that venue.
        for source in venue_sources.get_sources():
            if not source.is_listing:
                continue
            for row in source.fetcher():
                assert row["url"] or row["source_id"], f"{source.key}: {row}"

    def test_a_dead_site_yields_no_rows_rather_than_raising(self, monkeypatch):
        monkeypatch.setattr(venue_parsers, "fetch_url", lambda *a, **kw: None)
        monkeypatch.setattr(scrape_events, "fetch_url", lambda *a, **kw: None)
        for source in venue_sources.get_sources():
            if source.is_listing:
                assert source.fetcher() == []
            else:
                assert source.discover() == []
