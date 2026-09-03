"""Promoter notification emails and the dashboard opt-out toggle."""

from conftest import BrokenSMTP

from diytracker.models import db


def _edit_payload(event, label_id=""):
    return {
        "name": event.name,
        "date": event.date.strftime("%Y-%m-%d"),
        "doors": "20:00",
        "ticket_price": "10",
        "status": "scheduled",
        "venue_id": str(event.venue_id),
        "label_id": label_id,
    }


class TestAdminEditNotification:
    def test_admin_edit_of_labelled_event_emails_owner(
        self, client, admin, login, make_user, make_label, make_event, fake_smtp
    ):
        owner = make_user(email="promo@example.com", is_promoter=True)
        label = make_label(owner)
        event = make_event(label_id=label.id)
        login(admin)
        resp = client.post(
            f"/admin/edit_event/{event.id}",
            data=_edit_payload(event, label_id=str(label.id)),
        )
        assert resp.status_code == 302
        assert len(fake_smtp.instances) == 1
        msg = fake_smtp.instances[0].sent_message
        assert msg["To"] == "promo@example.com"
        assert event.name in msg["Subject"]

    def test_opt_out_suppresses_email(
        self, client, admin, login, make_user, make_label, make_event, fake_smtp
    ):
        owner = make_user(email="promo@example.com", is_promoter=True)
        owner.notify_label_events = False
        db.session.commit()
        label = make_label(owner)
        event = make_event(label_id=label.id)
        login(admin)
        client.post(
            f"/admin/edit_event/{event.id}",
            data=_edit_payload(event, label_id=str(label.id)),
        )
        assert fake_smtp.instances == []

    def test_no_email_for_unlabelled_event(
        self, client, admin, login, make_event, fake_smtp
    ):
        event = make_event()
        login(admin)
        client.post(f"/admin/edit_event/{event.id}", data=_edit_payload(event))
        assert fake_smtp.instances == []

    def test_owner_editing_own_labelled_event_not_emailed(
        self, client, login, make_user, make_label, make_event, fake_smtp
    ):
        owner = make_user(email="promo@example.com", is_promoter=True, is_admin=True)
        label = make_label(owner)
        event = make_event(label_id=label.id)
        login(owner)
        client.post(
            f"/admin/edit_event/{event.id}",
            data=_edit_payload(event, label_id=str(label.id)),
        )
        assert fake_smtp.instances == []

    def test_smtp_failure_does_not_break_edit(
        self, client, admin, login, make_user, make_label, make_event, fake_smtp
    ):
        fake_smtp.use(BrokenSMTP)
        owner = make_user(email="promo@example.com", is_promoter=True)
        label = make_label(owner)
        event = make_event(name="Before Edit", label_id=label.id)
        login(admin)
        payload = _edit_payload(event, label_id=str(label.id))
        payload["name"] = "After Edit"
        resp = client.post(f"/admin/edit_event/{event.id}", data=payload)
        assert resp.status_code == 302
        db.session.refresh(event)
        assert event.name == "After Edit"


class TestNotifyToggle:
    def test_toggle_flips_flag(self, client, make_user, login):
        user = make_user(is_promoter=True)
        login(user)
        assert user.notify_label_events is True
        client.post("/promoter/notifications")
        db.session.refresh(user)
        assert user.notify_label_events is False
        client.post("/promoter/notifications")
        db.session.refresh(user)
        assert user.notify_label_events is True


class TestDashboardPanels:
    def test_sparkline_renders_with_view_data(
        self, client, make_user, make_label, make_event, login
    ):
        from datetime import date

        from diytracker.models import EventDailyViews

        promoter = make_user(is_promoter=True)
        label = make_label(promoter)
        event = make_event(label_id=label.id)
        db.session.add(
            EventDailyViews(event_id=event.id, date=date.today(), hits=5, visitors=3)
        )
        db.session.commit()
        login(promoter)
        html = client.get("/promoter/").data.decode()
        assert "<svg" in html
        assert "polyline" in html

    def test_queue_match_panel(self, client, make_user, make_label, login):
        from datetime import date, timedelta

        from diytracker.models import ScrapedEvent

        promoter = make_user(is_promoter=True)
        make_label(promoter, name="Kälte Kollektiv")
        rec = ScrapedEvent(
            source="konzibot",
            url="https://x.ch/queue-match",
            title="Kälte Kollektiv Abend",
            venue_name="Hall",
            city="Bern",
            start_date=date.today() + timedelta(days=7),
        )
        db.session.add(rec)
        db.session.commit()
        login(promoter)
        resp = client.get("/promoter/")
        assert b"In review" in resp.data
        assert "Kälte Kollektiv Abend".encode() in resp.data

    def test_no_panel_without_matches(self, client, make_user, make_label, login):
        promoter = make_user(is_promoter=True)
        make_label(promoter, name="Cool Label")
        login(promoter)
        # The tutorial block quotes the panel's heading, so match on the
        # explanatory line that only the panel itself renders.
        assert b"waiting for admin approval" not in client.get("/promoter/").data
