"""Event submission & scrape-queue approval flow (the app's core workflow)."""

from datetime import date, datetime, time, timedelta

from models import db, Event, ScrapedEvent, Venue


def _future(days=14):
    return (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")


class TestSubmitEvent:
    def test_submit_creates_event_and_new_venue(self, client, make_user, login):
        login(make_user())
        resp = client.post(
            "/submit",
            data={
                "name": "New Show",
                "date": _future(),
                "doors": "20:00",
                "genre": ["Punk", "Hardcore"],
                "acts": "The Band",
                "ticket_price": "15",
                "venue_id": "new",
                "venue_name": "The Cave",
                "venue_city": "Bern",
                "venue_plz": "3000",
                "venue_canton": "BE",
            },
        )
        assert resp.status_code == 302
        ev = Event.query.filter_by(name="New Show").one()
        assert ev.venue.name == "The Cave"
        assert ev.genre == "Punk, Hardcore"
        assert ev.parent_genres == ",Hardcore,Punk,"  # hook ran, ordered

    def test_submit_reuses_existing_venue(self, client, make_user, login, make_venue):
        login(make_user())
        venue = make_venue(name="Dachstock", city="Bern", plz="3000")
        client.post(
            "/submit",
            data={
                "name": "Reuse Show",
                "date": _future(),
                "doors": "21:00",
                "ticket_price": "10",
                "venue_id": str(venue.id),
            },
        )
        ev = Event.query.filter_by(name="Reuse Show").one()
        assert ev.venue_id == venue.id
        assert Venue.query.count() == 1

    def test_new_venue_missing_details_is_rejected(self, client, make_user, login):
        login(make_user())
        resp = client.post(
            "/submit",
            data={
                "name": "Bad",
                "date": _future(),
                "doors": "20:00",
                "ticket_price": "5",
                "venue_id": "new",
                "venue_name": "No City Venue",  # missing city + plz
            },
            follow_redirects=True,
        )
        assert b"required venue details" in resp.data
        assert Event.query.count() == 0

    def test_nonexistent_selected_venue_rejected(self, client, make_user, login):
        login(make_user())
        resp = client.post(
            "/submit",
            data={
                "name": "Bad",
                "date": _future(),
                "doors": "20:00",
                "ticket_price": "5",
                "venue_id": "9999",
            },
            follow_redirects=True,
        )
        assert b"does not exist" in resp.data
        assert Event.query.count() == 0

    def test_submit_requires_login(self, client):
        resp = client.post("/submit", data={"name": "x"})
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]


class TestEventQueue:
    def _make_scraped(self, **kw):
        defaults = dict(
            source="metalgigs",
            url="https://metalgigs.ch/konzerte/show-1",
            title="Scraped Show",
            performers="Headliner",
            styles="Metal",
            start_date=date.today() + timedelta(days=20),
            doors_open=time(19, 0),
            venue_name="Hall",
            city="Aarau",
            postal_code="5000",
            region="AG",
            approved=False,
        )
        defaults.update(kw)
        rec = ScrapedEvent(**defaults)
        db.session.add(rec)
        db.session.commit()
        return rec

    def test_queue_lists_pending_scraped_events(self, client, admin, login):
        login(admin)
        self._make_scraped(title="Pending One")
        resp = client.get("/queue")
        assert resp.status_code == 200
        assert b"Pending One" in resp.data

    def test_approving_creates_event_and_marks_scraped(self, client, admin, login):
        login(admin)
        rec = self._make_scraped()
        resp = client.post("/queue", data={"scraped_id": str(rec.id)})
        assert resp.status_code == 302

        ev = Event.query.one()
        assert ev.name == "Scraped Show"
        assert ev.venue.name == "Hall"

        db.session.refresh(rec)
        assert rec.approved is True
        assert rec.approved_event_id == ev.id

    def test_duplicate_event_is_rejected_by_hash(self, client, admin, login):
        login(admin)
        # Two distinct scraped rows that resolve to identical event content
        # (same venue + name + date + …) -> identical event_hash.
        common = dict(
            title="Same Gig",
            styles="Punk",
            venue_name="Hall",
            city="Aarau",
            postal_code="5000",
            region="AG",
            start_date=date.today() + timedelta(days=20),
            doors_open=time(19, 0),
            performers="Headliner",
        )
        rec_a = self._make_scraped(url="https://metalgigs.ch/konzerte/a", **common)
        rec_b = self._make_scraped(url="https://metalgigs.ch/konzerte/b", **common)

        # Approve the first -> creates the event, marks that record approved.
        client.post("/queue", data={"scraped_id": str(rec_a.id)})
        assert Event.query.count() == 1

        # Approving the second hits the duplicate-hash guard.
        resp = client.post(
            "/queue", data={"scraped_id": str(rec_b.id)}, follow_redirects=True
        )
        assert b"already exists" in resp.data
        assert Event.query.count() == 1

    def test_invalid_scraped_id_flashes_error(self, client, admin, login):
        login(admin)
        resp = client.post("/queue", data={"scraped_id": "99"}, follow_redirects=True)
        assert b"Invalid event selection" in resp.data
        assert Event.query.count() == 0

    def test_already_approved_id_rejected(self, client, admin, login):
        login(admin)
        rec = self._make_scraped(approved=True)
        resp = client.post(
            "/queue", data={"scraped_id": str(rec.id)}, follow_redirects=True
        )
        assert b"Invalid event selection" in resp.data
        assert Event.query.count() == 0

    def test_override_fields_take_precedence(self, client, admin, login):
        login(admin)
        rec = self._make_scraped(title="Original Name")
        client.post(
            "/queue",
            data={
                "scraped_id": str(rec.id),
                "override_name": "Edited Name",
                "override_genre": "Techno",
            },
        )
        ev = Event.query.one()
        assert ev.name == "Edited Name"
        assert ev.genre == "Techno"

    def test_source_filter(self, client, admin, login):
        login(admin)
        self._make_scraped(
            title="MG", source="metalgigs", url="https://metalgigs.ch/konzerte/a"
        )
        self._make_scraped(
            title="Petzi", source="petzi", url="https://petzi.ch/en/events/b"
        )
        resp = client.get("/queue?source=petzi")
        assert b"Petzi" in resp.data
        assert b">MG<" not in resp.data  # crude, but MG title shouldn't render

    def test_delete_from_queue_marks_approved(self, client, admin, login):
        login(admin)
        rec = self._make_scraped()
        resp = client.post(f"/queue/{rec.id}/delete")
        assert resp.status_code == 302
        db.session.refresh(rec)
        assert rec.approved is True
        # No Event created by a delete.
        assert Event.query.count() == 0

    def test_queue_requires_login(self, client):
        resp = client.get("/queue")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]
