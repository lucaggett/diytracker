"""Model-level integration tests.

Focus on behaviour the models add on top of the ORM: the parent_genres sync
hook, password hashing, and token generation.
"""

from datetime import datetime

import pytest
from sqlalchemy.exc import IntegrityError

from diytracker.models import db, Event, ScrapedEvent, Submitter


class TestParentGenresHook:
    """Event.parent_genres is derived automatically via SA insert/update hooks."""

    def _make_event(self, make_venue, genre):
        from datetime import time, timedelta
        from diytracker.services.events import compute_event_hash

        venue = make_venue()
        date = datetime.now() + timedelta(days=5)
        ev = Event(
            name="X",
            date=date,
            doors=time(20, 0),
            genre=genre,
            acts="",
            ticket_price="10",
            venue_id=venue.id,
            event_hash=compute_event_hash(
                "X", date, time(20, 0), genre, "", "", "10", venue.id
            ),
        )
        db.session.add(ev)
        db.session.commit()
        return ev

    def test_synced_on_insert(self, make_venue):
        ev = self._make_event(make_venue, "Black Metal, Hardcore")
        # Sentinel commas wrap the comma-joined parent list.
        assert ev.parent_genres == ",Metal,Hardcore,"

    def test_synced_on_update(self, make_venue):
        ev = self._make_event(make_venue, "Punk")
        assert ev.parent_genres == ",Punk,"
        ev.genre = "Techno"
        db.session.commit()
        assert ev.parent_genres == ",Electronic,"

    def test_null_for_no_recognised_genre(self, make_venue):
        ev = self._make_event(make_venue, "Concert")  # pure noise -> no parents
        assert ev.parent_genres is None

    def test_sentinel_enables_substring_filtering(self, make_venue):
        """The ',Parent,' sentinel form is what lets queries use LIKE safely."""
        self._make_event(make_venue, "Metalcore")  # -> Hardcore
        match = Event.query.filter(Event.parent_genres.like("%,Hardcore,%")).all()
        assert len(match) == 1


class TestSubmitter:
    def test_password_roundtrip(self, app):
        u = Submitter(email="a@b.com")
        u.set_password("hunter2")
        assert u.check_password("hunter2") is True
        assert u.check_password("wrong") is False

    def test_check_password_without_hash(self, app):
        u = Submitter(email="a@b.com")
        assert u.check_password("anything") is False

    def test_default_submission_code_is_unique(self, app):
        u1, u2 = Submitter(email="a@b.com"), Submitter(email="c@d.com")
        assert u1.submission_code and u2.submission_code
        assert u1.submission_code != u2.submission_code

    def test_invite_token_lifecycle(self, app):
        u = Submitter(email="a@b.com")
        token = u.generate_invite_token()
        assert token and u.invite_token == token
        assert u.invite_token_expiry > datetime.now()
        u.clear_invite_token()
        assert u.invite_token is None and u.invite_token_expiry is None


class TestScrapedEvent:
    def test_url_unique_constraint(self, app):
        db.session.add(ScrapedEvent(url="https://example.com/gig"))
        db.session.commit()
        db.session.add(ScrapedEvent(url="https://example.com/gig"))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


class TestVenue:
    def test_accessibility_token_generation(self, make_venue):
        venue = make_venue()
        assert venue.accessibility_token is None
        token = venue.generate_accessibility_token()
        assert token and venue.accessibility_token == token
