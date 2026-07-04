"""Admin dashboard, event/venue management, and exports."""

import re

from diytracker.models import db, Event, ScrapedEvent, Venue


class TestEventManagement:
    def test_edit_event_updates_fields(self, client, admin, login, make_event):
        login(admin)
        ev = make_event(name="Before")
        resp = client.post(
            f"/edit_event/{ev.id}",
            data={
                "name": "After",
                "date": ev.date.strftime("%Y-%m-%d"),
                "doors": "22:00",
                "genre": ["Techno"],
                "acts": "DJ",
                "ticket_price": "25",
                "venue_id": str(ev.venue_id),
            },
        )
        assert resp.status_code == 302
        db.session.refresh(ev)
        assert ev.name == "After"
        assert ev.genre == "Techno"
        assert ev.parent_genres == ",Electronic,"

    def test_edit_requires_admin(self, client, make_user, login, make_event):
        ev = make_event()
        login(make_user(is_admin=False))
        assert client.get(f"/edit_event/{ev.id}").status_code == 403

    def test_delete_event(self, client, admin, login, make_event):
        login(admin)
        ev = make_event()
        resp = client.post(f"/delete_event/{ev.id}")
        assert resp.status_code == 302
        assert Event.query.get(ev.id) is None

    def test_edit_missing_event_404(self, client, admin, login):
        login(admin)
        assert client.get("/edit_event/9999").status_code == 404


class TestVenueManagement:
    def test_venue_list(self, client, admin, login, make_venue):
        login(admin)
        make_venue(name="Rote Fabrik")
        resp = client.get("/admin/venues")
        assert resp.status_code == 200
        assert b"Rote Fabrik" in resp.data

    def test_edit_venue(self, client, admin, login, make_venue):
        login(admin)
        v = make_venue(name="Old Name")
        resp = client.post(
            f"/admin/venues/{v.id}/edit",
            data={
                "name": "New Name",
                "city": "Basel",
                "plz": "4000",
                "canton": "BS",
            },
        )
        assert resp.status_code == 302
        db.session.refresh(v)
        assert v.name == "New Name"
        assert v.city == "Basel"

    def test_delete_empty_venue(self, client, admin, login, make_venue):
        login(admin)
        v = make_venue()
        resp = client.post(f"/admin/venues/{v.id}/delete")
        assert resp.status_code == 302
        assert Venue.query.get(v.id) is None

    def test_cannot_delete_venue_with_events(self, client, admin, login, make_event):
        login(admin)
        ev = make_event()
        venue_id = ev.venue_id
        resp = client.post(f"/admin/venues/{venue_id}/delete", follow_redirects=True)
        assert b"Cannot delete venue" in resp.data
        assert Venue.query.get(venue_id) is not None

    def test_generate_accessibility_link(self, client, admin, login, make_venue):
        login(admin)
        v = make_venue()
        resp = client.get(f"/admin/venues/{v.id}/accessibility-link")
        assert resp.status_code == 302
        db.session.refresh(v)
        assert v.accessibility_token is not None


class TestExportsAndStatus:
    def test_excel_export(self, client, admin, login, make_event):
        login(admin)
        make_event()
        resp = client.get("/admin/export-excel")
        assert resp.status_code == 200
        assert "spreadsheetml" in resp.headers["Content-Type"]
        assert resp.headers["Content-Disposition"].startswith("attachment")
        # XLSX is a zip archive -> starts with PK.
        assert resp.data[:2] == b"PK"

    def test_scrape_status_idle(self, client, admin, login):
        login(admin)
        resp = client.get("/admin/scrape-status")
        assert resp.get_json() == {"running": False}

    def test_export_requires_admin(self, client, make_user, login):
        login(make_user(is_admin=False))
        assert client.get("/admin/export-excel").status_code == 403


class TestLeaderboard:
    def _approve(self, event):
        """Mark an event as having been created via queue approval."""
        rec = ScrapedEvent(approved=True, approved_event_id=event.id)
        db.session.add(rec)
        db.session.commit()
        return rec

    def test_requires_admin(self, client, make_user, login):
        login(make_user(is_admin=False))
        assert client.get("/admin/leaderboard").status_code == 403

    def test_ranking_and_score(self, client, admin, login, make_user, make_event):
        login(admin)
        alice = make_user(email="alice@example.com")
        bob = make_user(email="bob@example.com")
        # Alice: 1 direct submission + 2 queue approvals -> score 2.
        make_event(name="A1", submitter_id=alice.id)
        self._approve(make_event(name="A2", submitter_id=alice.id))
        self._approve(make_event(name="A3", submitter_id=alice.id))
        # Bob: 3 direct submissions -> score 3.
        for n in ("B1", "B2", "B3"):
            make_event(name=n, submitter_id=bob.id)

        resp = client.get("/admin/leaderboard")
        assert resp.status_code == 200
        body = resp.data.decode()
        # Bob (score 3) ranks above Alice (score 2), who ranks above the
        # admin (score 0).
        assert (
            body.index("bob@example.com")
            < body.index("alice@example.com")
            < body.index("admin@example.com")
        )
        # Alice's row shows submitted=1, approved=2, score=2 as its numbers.
        alice_row = body[body.index("alice@example.com") : body.index("admin@")]
        numbers = re.findall(r">\s*([\d.]+)\s*<", alice_row)
        assert numbers[:3] == ["1", "2", "2"]

    def test_half_point_score_displayed(self, client, admin, login, make_event):
        login(admin)
        self._approve(make_event(name="Q1", submitter_id=admin.id))
        resp = client.get("/admin/leaderboard")
        assert b"0.5" in resp.data
