"""Foreign keys are enforced (PRAGMA foreign_keys=ON per connection), so
parent deletes must detach or refuse — a venue delete that orphans events
would otherwise 500 every affected event page (prod incident, event 283)."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from diytracker.admin.core import AdminError
from diytracker.admin.users import delete_user
from diytracker.models import db, ScrapedEvent, Venue, VenueAccessibility


class TestForeignKeyEnforcement:
    def test_raw_venue_delete_with_events_is_rejected(self, app, make_event):
        ev = make_event()
        with pytest.raises(IntegrityError):
            db.session.execute(
                text("DELETE FROM venue WHERE id = :id"), {"id": ev.venue_id}
            )
        db.session.rollback()

    def test_admin_delete_venue_removes_accessibility_row(
        self, client, admin, login, make_venue
    ):
        login(admin)
        v = make_venue()
        db.session.add(VenueAccessibility(venue_id=v.id, updated_at=db.func.now()))
        db.session.commit()
        venue_id = v.id
        resp = client.post(f"/admin/venues/{venue_id}/delete")
        assert resp.status_code == 302
        assert db.session.get(Venue, venue_id) is None
        assert VenueAccessibility.query.filter_by(venue_id=venue_id).first() is None

    def test_admin_delete_event_detaches_scrape_approval(
        self, client, admin, login, make_event
    ):
        login(admin)
        ev = make_event()
        rec = ScrapedEvent(
            status=ScrapedEvent.STATUS_PUBLISHED, approved_event_id=ev.id
        )
        db.session.add(rec)
        db.session.commit()
        resp = client.post(f"/admin/delete_event/{ev.id}")
        assert resp.status_code == 302
        db.session.refresh(rec)
        assert rec.approved_event_id is None
        assert rec.status == ScrapedEvent.STATUS_PUBLISHED

    def test_delete_user_owning_labels_is_refused(self, app, make_user, make_label):
        promoter = make_user(email="promo@example.com", is_promoter=True)
        make_label(promoter)
        with pytest.raises(AdminError, match="label"):
            delete_user("promo@example.com")
        assert db.session.get(type(promoter), promoter.id) is not None
