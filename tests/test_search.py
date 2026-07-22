"""Free-text search: the service and the /search page."""

from diytracker.services.search import (
    MAX_QUERY_LENGTH,
    normalise_query,
    search_events,
    search_venues,
)


class TestNormalise:
    def test_collapses_whitespace(self):
        assert normalise_query("  punk   bern \n") == "punk bern"

    def test_caps_length(self):
        assert len(normalise_query("x" * 500)) == MAX_QUERY_LENGTH

    def test_empty_input(self):
        assert normalise_query(None) == ""


class TestSearchEvents:
    def test_matches_event_name(self, app, make_event):
        make_event(name="Grind Night")
        assert [e.name for e in search_events("grind")] == ["Grind Night"]

    def test_matches_act(self, app, make_event):
        make_event(name="Untitled", acts="Bolzer, Zeal & Ardor")
        assert len(search_events("bolzer")) == 1

    def test_matches_venue_name(self, app, make_event, make_venue):
        make_event(name="At Ebrietas", venue=make_venue(name="Ebrietas"))
        assert len(search_events("ebrietas")) == 1

    def test_matches_city(self, app, make_event, make_venue):
        make_event(venue=make_venue(name="Rössli", city="Bern", plz="3000"))
        assert len(search_events("bern")) == 1

    def test_multiple_terms_are_anded(self, app, make_event, make_venue):
        bern = make_venue(name="Rössli", city="Bern", plz="3000")
        zurich = make_venue(name="Kasheme", city="Zürich")
        make_event(name="Punk Night", venue=bern, genre="Punk")
        make_event(name="Punk Night", venue=zurich, genre="Punk")
        results = search_events("punk bern")
        assert len(results) == 1
        assert results[0].venue.city == "Bern"

    def test_wildcards_in_input_are_escaped(self, app, make_event):
        make_event(name="Grind Night")
        # Unescaped, "%" would match every row.
        assert search_events("%") == []

    def test_underscore_is_escaped(self, app, make_event):
        make_event(name="Grind Night")
        assert search_events("_") == []

    def test_past_events_excluded_by_default(self, app, make_event):
        make_event(name="Old Show", days_from_now=-30)
        assert search_events("old show") == []
        assert len(search_events("old show", include_past=True)) == 1

    def test_empty_query_returns_nothing(self, app, make_event):
        make_event()
        assert search_events("") == []
        assert search_events("   ") == []


class TestSearchVenues:
    def test_matches_name_and_city(self, app, make_venue):
        make_venue(name="Ebrietas", city="Zürich")
        assert len(search_venues("ebrietas")) == 1
        assert len(search_venues("zürich")) == 1

    def test_empty_query_returns_nothing(self, app, make_venue):
        make_venue()
        assert search_venues("") == []


class TestSearchPage:
    def test_empty_query_renders_prompt(self, client):
        resp = client.get("/search")
        assert resp.status_code == 200
        assert b"Type a band" in resp.data

    def test_finds_an_event(self, client, make_event):
        make_event(name="Findable Show")
        assert b"Findable Show" in client.get("/search?q=findable").data

    def test_no_match_message(self, client, make_event):
        make_event(name="Findable Show")
        resp = client.get("/search?q=zzzznothing")
        assert resp.status_code == 200
        assert b"Findable Show" not in resp.data

    def test_past_toggle_includes_past_events(self, client, make_event):
        make_event(name="Old Show", days_from_now=-30)
        assert b"Old Show" not in client.get("/search?q=old+show").data
        assert b"Old Show" in client.get("/search?q=old+show&past=1").data

    def test_results_are_noindex(self, client, make_event):
        make_event(name="Findable Show")
        html = client.get("/search?q=findable").data.decode()
        assert 'name="robots"' in html and "noindex" in html

    def test_robots_txt_disallows_search(self, client):
        assert b"Disallow: /search" in client.get("/robots.txt").data

    def test_search_is_reachable_from_every_page(self, client):
        # The form action is locale-prefixed for non-default locales (/en/search).
        assert b"/search" in client.get("/").data

    def test_canton_pages_still_resolve(self, client, make_event, make_venue):
        # /search and /calendar.ics joined RESERVED_SLUGS; the canton converter
        # must still match real canton slugs.
        make_event(venue=make_venue(city="Zürich", canton="ZH"))
        assert client.get("/zuerich/").status_code == 200
