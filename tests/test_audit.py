"""The ActionLog audit trail: rows are written alongside the actions they
describe.

Only the web half lives here now. Rows written with actor="tui" and the
reader behind the Audit log screen belong to the diytracker-admin repo.
"""

from datetime import date, time, timedelta

from diytracker.models import ActionLog, Event, ScrapedEvent, db
from diytracker.services import queue_review


def _make_scraped(**kw):
    defaults = {
        "source": "metalgigs",
        "url": "https://metalgigs.ch/konzerte/audit-1",
        "title": "Audit Show",
        "performers": "Headliner",
        "styles": "Metal",
        "start_date": date.today() + timedelta(days=20),
        "doors_open": time(19, 0),
        "venue_name": "Hall",
        "city": "Aarau",
        "postal_code": "5000",
        "region": "AG",
        "organizer": "Kollektiv X",
        "approved": False,
    }
    defaults.update(kw)
    rec = ScrapedEvent(**defaults)
    db.session.add(rec)
    db.session.commit()
    return rec


class TestQueueAudit:
    def test_approve_logs_scraped_attribution(self, client, admin, login):
        login(admin)
        rec = _make_scraped()
        client.post("/queue", data={"scraped_id": str(rec.id)})
        row = ActionLog.query.filter_by(action="queue.approve").one()
        assert row.actor == admin.email
        assert row.actor_id == admin.id
        assert row.target_type == "scraped_event"
        assert row.target_id == rec.id
        assert "Kollektiv X" in row.detail
        assert f"event_id={Event.query.one().id}" in row.detail

    def test_web_reject_logs(self, client, admin, login):
        login(admin)
        rec = _make_scraped()
        client.post(f"/queue/{rec.id}/delete")
        row = ActionLog.query.filter_by(action="queue.reject").one()
        assert row.actor == admin.email
        assert row.target_id == rec.id
        assert "Audit Show" in row.detail

    def test_queue_review_discard_and_unflag_log(self, app):
        # queue_review backs /queue/duplicates; its rows are attributed to the
        # caller that ran it, which for these direct calls is "tui".
        rec = _make_scraped(needs_review=True, review_reason="same-day collision")
        queue_review.unflag(rec.id)
        rec2 = _make_scraped(
            url="https://metalgigs.ch/konzerte/audit-2",
            needs_review=True,
            review_reason="same-day collision",
        )
        queue_review.discard(rec2.id)
        unflag_row = ActionLog.query.filter_by(action="queue.unflag").one()
        reject_row = ActionLog.query.filter_by(action="queue.reject").one()
        assert unflag_row.actor == "tui"
        assert unflag_row.actor_id is None
        assert reject_row.target_id == rec2.id


class TestEventAudit:
    def test_admin_edit_logs_label_change(
        self, client, admin, login, make_event, make_user, make_label
    ):
        label = make_label(make_user(email="promo@example.com", is_promoter=True))
        event = make_event()
        login(admin)
        client.post(
            f"/admin/edit_event/{event.id}",
            data={
                "name": event.name,
                "date": event.date.strftime("%Y-%m-%d"),
                "doors": "20:00",
                "ticket_price": "10",
                "status": "scheduled",
                "venue_id": str(event.venue_id),
                "label_id": str(label.id),
            },
        )
        row = ActionLog.query.filter_by(action="event.edit").one()
        assert row.target_id == event.id
        assert f"label None->{label.id}" in row.detail

    def test_admin_delete_logs_snapshot(self, client, admin, login, make_event):
        event = make_event(name="Doomed Show")
        event_id = event.id
        login(admin)
        client.post(f"/admin/delete_event/{event_id}")
        row = ActionLog.query.filter_by(action="event.delete").one()
        assert row.target_id == event_id
        assert "Doomed Show" in row.detail
        # The log row outlives its target.
        assert db.session.get(Event, event_id) is None


class TestLabelAudit:
    def test_claim_logs(self, client, make_user, make_label, make_event, login):
        promoter = make_user(email="promo@example.com", is_promoter=True)
        label = make_label(promoter)
        event = make_event()
        login(promoter)
        client.post(
            f"/promoter/events/{event.id}/claim", data={"label_id": str(label.id)}
        )
        row = ActionLog.query.filter_by(action="label.claim").one()
        assert row.actor == promoter.email
        assert row.target_type == "event"
        assert row.target_id == event.id
        assert f"label_id={label.id}" in row.detail
