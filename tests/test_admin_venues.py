"""Tests for the venue admin logic (diytracker/admin/venues.py).

Dedup lives in test_admin_tool.py / test_venue_dedup.py; this covers the
browse-and-fix half: listing, search, editing and deleting.
"""

import dataclasses

import pytest

from diytracker.admin import venues
from diytracker.admin.core import AdminError
from diytracker.models import Venue, VenueAccessibility, db, utcnow


class TestListVenues:
    def test_lists_with_event_counts(self, app, make_venue, make_event):
        with app.app_context():
            venue = make_venue(name="Rote Fabrik", city="Zürich")
            make_event(venue=venue)
            make_venue(name="Kasheme", city="Basel")
            rows = venues.list_venues()
        by_name = {row.name: row for row in rows}
        assert by_name["Rote Fabrik"].n_events == 1
        assert by_name["Kasheme"].n_events == 0
        assert [row.name for row in rows] == ["Kasheme", "Rote Fabrik"]

    def test_search_ands_its_terms(self, app, make_venue):
        with app.app_context():
            make_venue(name="Rote Fabrik", city="Zürich")
            make_venue(name="Rote Falle", city="Bern", canton="BE")
            assert [r.name for r in venues.list_venues("rote")] == [
                "Rote Fabrik",
                "Rote Falle",
            ]
            assert [r.name for r in venues.list_venues("rote bern")] == ["Rote Falle"]

    def test_search_escapes_wildcards(self, app, make_venue):
        with app.app_context():
            make_venue(name="Kasheme")
            assert venues.list_venues("%") == []

    def test_accessibility_presence_is_reported(self, app, make_venue):
        with app.app_context():
            venue = make_venue()
            db.session.add(VenueAccessibility(venue_id=venue.id, updated_at=utcnow()))
            db.session.commit()
            row = venues.get_venue(venue.id)
        assert row.a11y_updated  # a date, not empty

    def test_the_accessibility_token_never_leaves_the_module(self, app, make_venue):
        with app.app_context():
            venue = make_venue()
            token = venue.generate_accessibility_token()
            db.session.commit()
            row = venues.get_venue(venue.id)
        assert row.has_token is True
        values = [str(value) for value in dataclasses.asdict(row).values()]
        assert not any(token in value for value in values)


class TestUpdateVenue:
    def test_updates_fields(self, app, make_venue):
        with app.app_context():
            venue = make_venue(name="Kaschemme", city="Basel", plz="4057")
            row = venues.update_venue(venue.id, name="Kasheme", address="Rümelinsplatz")
        assert row.name == "Kasheme"
        assert row.address == "Rümelinsplatz"
        assert row.city == "Basel"  # untouched fields stay

    def test_canton_full_name_becomes_a_code(self, app, make_venue):
        with app.app_context():
            venue = make_venue(city="Zürich", canton="ZH")
            row = venues.update_venue(venue.id, canton="Zürich")
        assert row.canton == "ZH"

    @pytest.mark.parametrize("field", ["name", "city", "plz"])
    def test_required_fields_reject_empty(self, app, make_venue, field):
        with app.app_context():
            venue = make_venue()
            with pytest.raises(AdminError, match="cannot be empty"):
                venues.update_venue(venue.id, **{field: "  "})

    def test_optional_fields_clear_to_null(self, app, make_venue):
        with app.app_context():
            venue = make_venue(address="Somewhere 1")
            venues.update_venue(venue.id, address="")
            assert db.session.get(Venue, venue.id).address is None

    def test_unknown_field_raises(self, app, make_venue):
        with app.app_context():
            venue = make_venue()
            with pytest.raises(AdminError, match="editable"):
                venues.update_venue(venue.id, accessibility_token="nope")

    def test_missing_venue_raises(self, app):
        with app.app_context():
            with pytest.raises(AdminError, match="No venue"):
                venues.update_venue(4242, name="x")


class TestDeleteVenue:
    def test_refuses_while_events_point_at_it(self, app, make_venue, make_event):
        with app.app_context():
            venue = make_venue()
            make_event(venue=venue)
            with pytest.raises(AdminError, match="still has 1 event"):
                venues.delete_venue(venue.id)
            assert db.session.get(Venue, venue.id) is not None

    def test_deletes_venue_and_its_accessibility_row(self, app, make_venue):
        with app.app_context():
            venue = make_venue()
            db.session.add(VenueAccessibility(venue_id=venue.id, updated_at=utcnow()))
            db.session.commit()
            venue_id = venue.id

            assert venues.delete_venue(venue_id) == "Kasheme"
            assert db.session.get(Venue, venue_id) is None
            assert VenueAccessibility.query.filter_by(venue_id=venue_id).count() == 0

    def test_missing_venue_raises(self, app):
        with app.app_context():
            with pytest.raises(AdminError, match="No venue"):
                venues.delete_venue(4242)
