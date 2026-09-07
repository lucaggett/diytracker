"""iCalendar serialisation and the .ics routes."""

from datetime import timedelta

from diytracker.models import db
from diytracker.services.ics import (
    build_calendar,
    escape,
    event_bounds,
    fold,
)


def _lines(body):
    """Unfold a serialised calendar back into logical lines."""
    out = []
    for raw in body.split("\r\n"):
        if raw.startswith(" ") and out:
            out[-1] += raw[1:]
        elif raw:
            out.append(raw)
    return out


class TestEscaping:
    def test_escapes_special_characters(self):
        assert escape("A, B; C\\D\nE") == "A\\, B\\; C\\\\D\\nE"

    def test_carriage_returns_collapse_to_one_newline(self):
        assert escape("A\r\nB") == "A\\nB"


class TestFolding:
    def test_short_line_untouched(self):
        assert fold("SUMMARY:hi") == "SUMMARY:hi"

    def test_long_line_folds_at_75_octets(self):
        folded = fold("SUMMARY:" + "x" * 200)
        segments = folded.split("\r\n")
        assert len(segments[0].encode()) <= 75
        assert all(seg.startswith(" ") for seg in segments[1:])

    def test_multibyte_characters_are_not_split(self):
        # Every fold point must land on a character boundary, or the output
        # is invalid UTF-8 — venue names are full of umlauts.
        folded = fold("LOCATION:" + "ü" * 120)
        for segment in folded.split("\r\n"):
            segment.encode("utf-8").decode("utf-8")
        assert folded.replace("\r\n ", "") == "LOCATION:" + "ü" * 120


class TestBuildCalendar:
    def test_one_vevent_per_event(self, app, make_event):
        with app.test_request_context():
            body = build_calendar([make_event(), make_event(name="Second")], "test")
        assert body.count("BEGIN:VEVENT") == 2
        assert body.startswith("BEGIN:VCALENDAR\r\n")
        assert body.endswith("END:VCALENDAR\r\n")

    def test_uid_is_stable_and_event_scoped(self, app, make_event):
        event = make_event()
        with app.test_request_context():
            first = build_calendar([event], "test")
            second = build_calendar([event], "test")
        uid = f"UID:event-{event.id}@diytracker.ch"
        assert uid in _lines(first)
        assert _lines(first) == _lines(second)

    def test_times_carry_the_zurich_timezone(self, app, make_event):
        with app.test_request_context():
            body = build_calendar([make_event()], "test")
        lines = _lines(body)
        assert any(line.startswith("DTSTART;TZID=Europe/Zurich:") for line in lines)
        assert "BEGIN:VTIMEZONE" in lines

    def test_summary_escapes_punctuation(self, app, make_event):
        with app.test_request_context():
            body = build_calendar([make_event(name="Grind, Noise; Doom")], "test")
        assert "SUMMARY:Grind\\, Noise\\; Doom" in _lines(body)

    def test_cancelled_event_is_marked_cancelled(self, app, make_event):
        with app.test_request_context():
            body = build_calendar([make_event(status="cancelled")], "test")
        assert "STATUS:CANCELLED" in _lines(body)

    def test_postponed_event_is_tentative(self, app, make_event):
        with app.test_request_context():
            body = build_calendar([make_event(status="postponed")], "test")
        assert "STATUS:TENTATIVE" in _lines(body)

    def test_unsafe_ticket_link_is_not_serialised(self, app, make_event):
        event = make_event(ticket_link="javascript:alert(1)")
        with app.test_request_context():
            body = build_calendar([event], "test")
        assert "javascript" not in body

    def test_description_carries_the_event_url(self, app, make_event):
        event = make_event()
        with app.test_request_context():
            body = build_calendar([event], "test")
        assert f"/events/{event.id}/" in body


class TestEventBounds:
    def test_default_duration_when_no_end_date(self, app, make_event):
        event = make_event()
        start, end = event_bounds(event)
        assert end - start == timedelta(hours=4)
        assert start.hour == event.doors.hour

    def test_festival_end_is_exclusive_midnight(self, app, make_event, make_venue):
        event = make_event(days_from_now=10)
        # A festival running through its end_date must cover that whole day,
        # so DTEND lands at midnight of the following day.
        event.end_date = event.date.date() + timedelta(days=2)
        db.session.commit()
        _start, end = event_bounds(event)
        assert end.date() == event.end_date + timedelta(days=1)
        assert (end.hour, end.minute) == (0, 0)


class TestRoutes:
    def test_event_ics_downloads(self, client, make_event):
        event = make_event(name="Single Show")
        resp = client.get(f"/events/{event.id}.ics")
        assert resp.status_code == 200
        assert "text/calendar" in resp.headers["Content-Type"]
        assert "attachment" in resp.headers["Content-Disposition"]
        assert b"SUMMARY:Single Show" in resp.data

    def test_event_ics_404_for_unknown_event(self, client):
        assert client.get("/events/999999.ics").status_code == 404

    def test_feed_lists_upcoming_events(self, client, make_event):
        make_event(name="Feed Show", days_from_now=5)
        resp = client.get("/calendar.ics")
        assert resp.status_code == 200
        assert "text/calendar" in resp.headers["Content-Type"]
        assert b"SUMMARY:Feed Show" in resp.data

    def test_feed_excludes_past_events(self, client, make_event):
        make_event(name="Long Gone", days_from_now=-30)
        assert b"Long Gone" not in client.get("/calendar.ics").data

    def test_feed_excludes_events_beyond_the_horizon(self, client, make_event):
        make_event(name="Far Future", days_from_now=400)
        assert b"Far Future" not in client.get("/calendar.ics").data

    def test_canton_filter_narrows(self, client, make_event, make_venue):
        zurich = make_venue(name="ZH Venue", city="Zürich", canton="ZH")
        bern = make_venue(name="BE Venue", city="Bern", canton="BE", plz="3000")
        make_event(name="Zurich Show", venue=zurich)
        make_event(name="Bern Show", venue=bern)
        data = client.get("/calendar.ics?canton=zuerich").data
        assert b"Zurich Show" in data
        assert b"Bern Show" not in data

    def test_unknown_canton_404s(self, client, make_event):
        make_event()
        # A shared subscription URL must never quietly widen into everything.
        assert client.get("/calendar.ics?canton=atlantis").status_code == 404

    def test_genre_filter_narrows(self, client, make_event):
        make_event(name="Punk Show", genre="Punk")
        make_event(name="Metal Show", genre="Death Metal")
        data = client.get("/calendar.ics?genre=punk").data
        assert b"Punk Show" in data
        assert b"Metal Show" not in data

    def test_unknown_genre_404s(self, client, make_event):
        make_event(genre="Punk")
        assert client.get("/calendar.ics?genre=nosuchgenre").status_code == 404

    def test_calendar_page_links_the_feed(self, client):
        html = client.get("/").data.decode()
        assert "/calendar.ics" in html
        assert 'type="text/calendar"' in html


class TestFeedCacheIsPerLocale:
    """The feed looks locale-free but isn't: the calendar description goes
    through _(), and the per-event URLs are built with url_for(), which the
    public blueprint prefixes from g.locale. Keying the cache on the filters
    alone froze the first requester's language into it — one French fetch and
    every later subscriber, in any language, got the French feed.
    """

    def _get(self, app, lang):
        c = app.test_client()
        c.environ_base["HTTP_ACCEPT_LANGUAGE"] = lang
        return c.get("/calendar.ics").get_data(as_text=True)

    def test_german_feed_is_not_served_the_french_one(self, app, make_event):
        make_event(name="Locale Probe")

        french = self._get(app, "fr")
        german = self._get(app, "de")

        assert "/fr/events/" in french
        assert "/fr/events/" not in german
        assert french != german

    def test_each_locale_keeps_its_own_description(self, app, make_event):
        make_event(name="Locale Probe")

        # Warm the cache in French first — the order is the whole point.
        self._get(app, "fr")
        italian = self._get(app, "it")

        assert "/it/events/" in italian
        assert "/fr/events/" not in italian

    def test_repeat_request_in_one_locale_is_still_cached(self, app, make_event):
        make_event(name="Locale Probe")
        assert self._get(app, "fr") == self._get(app, "fr")
