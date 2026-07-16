"""Promoter accounts, labels and the promoter dashboard."""

import io
from datetime import date, timedelta

from PIL import Image

from diytracker.models import db, Event, EventDailyViews, Label


def _png_upload(filename="logo.png"):
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), "black").save(buf, format="PNG")
    buf.seek(0)
    return (buf, filename)


class TestAccess:
    def test_anonymous_redirects_to_login(self, client):
        resp = client.get("/promoter/")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_plain_user_forbidden(self, client, make_user, login):
        login(make_user())
        assert client.get("/promoter/").status_code == 403

    def test_promoter_allowed(self, client, make_user, login):
        login(make_user(is_promoter=True))
        assert client.get("/promoter/").status_code == 200

    def test_admin_allowed(self, client, admin, login):
        login(admin)
        assert client.get("/promoter/").status_code == 200


class TestLabelCrud:
    def test_create_label_with_logo(self, client, make_user, login):
        login(make_user(is_promoter=True))
        resp = client.post(
            "/promoter/labels/new",
            data={"name": "Cool Label", "logo": _png_upload()},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 302
        label = Label.query.filter_by(name="Cool Label").one()
        assert label.slug == "cool-label"
        assert label.logo

    def test_create_label_with_description(self, client, make_user, login):
        login(make_user(is_promoter=True))
        client.post(
            "/promoter/labels/new",
            data={"name": "Descriptive", "description": "  DIY since day one.  "},
        )
        label = Label.query.filter_by(name="Descriptive").one()
        assert label.description == "DIY since day one."

    def test_edit_label_description_round_trips(
        self, client, make_user, make_label, login
    ):
        promoter = make_user(is_promoter=True)
        label = make_label(promoter)
        login(promoter)
        client.post(
            f"/promoter/labels/{label.id}/edit",
            data={"name": label.name, "description": "Basement shows."},
        )
        assert db.session.get(Label, label.id).description == "Basement shows."
        # The edit form pre-fills the saved description...
        html = client.get(f"/promoter/labels/{label.id}/edit").data.decode()
        assert "Basement shows." in html
        # ...and clearing the field stores NULL, not an empty string.
        client.post(
            f"/promoter/labels/{label.id}/edit",
            data={"name": label.name, "description": ""},
        )
        assert db.session.get(Label, label.id).description is None

    def test_duplicate_name_rejected_case_insensitive(
        self, client, make_user, make_label, login
    ):
        promoter = make_user(is_promoter=True)
        make_label(promoter, name="Cool Label")
        login(promoter)
        resp = client.post(
            "/promoter/labels/new",
            data={"name": "cool label"},
            follow_redirects=True,
        )
        assert b"already exists" in resp.data
        assert Label.query.count() == 1

    def test_slug_collision_gets_suffix(self, client, make_user, make_label, login):
        promoter = make_user(is_promoter=True)
        make_label(promoter, name="Cool Label")
        login(promoter)
        client.post("/promoter/labels/new", data={"name": "Cool-Label"})
        slugs = {label.slug for label in Label.query.all()}
        assert slugs == {"cool-label", "cool-label-2"}

    def test_rename_regenerates_slug(self, client, make_user, make_label, login):
        promoter = make_user(is_promoter=True)
        label = make_label(promoter, name="Old Name")
        login(promoter)
        client.post(f"/promoter/labels/{label.id}/edit", data={"name": "New Name"})
        assert db.session.get(Label, label.id).slug == "new-name"

    def test_non_owner_promoter_forbidden(self, client, make_user, make_label, login):
        owner = make_user(email="owner@example.com", is_promoter=True)
        label = make_label(owner)
        login(make_user(email="other@example.com", is_promoter=True))
        assert client.get(f"/promoter/labels/{label.id}/edit").status_code == 403

    def test_admin_may_edit_any_label(
        self, client, make_user, make_label, admin, login
    ):
        label = make_label(make_user(is_promoter=True))
        login(admin)
        assert client.get(f"/promoter/labels/{label.id}/edit").status_code == 200

    def test_delete_detaches_events(
        self, client, make_user, make_label, make_event, login
    ):
        promoter = make_user(is_promoter=True)
        label = make_label(promoter)
        event = make_event(label_id=label.id)
        login(promoter)
        resp = client.post(f"/promoter/labels/{label.id}/delete")
        assert resp.status_code == 302
        assert Label.query.count() == 0
        assert db.session.get(Event, event.id).label_id is None


class TestClaim:
    def test_plain_user_forbidden(self, client, make_user, login):
        login(make_user())
        assert client.get("/promoter/claim").status_code == 403

    def test_lists_only_unclaimed_upcoming_events(
        self, client, make_user, make_label, make_event, login
    ):
        promoter = make_user(is_promoter=True)
        label = make_label(promoter)
        make_event(name="Unclaimed Show")
        make_event(name="Claimed Show", label_id=label.id)
        make_event(name="Past Show", days_from_now=-10)
        login(promoter)
        resp = client.get("/promoter/claim")
        assert b"Unclaimed Show" in resp.data
        assert b"Claimed Show" not in resp.data
        assert b"Past Show" not in resp.data

    def test_no_labels_shows_hint(self, client, make_user, make_event, login):
        make_event()
        login(make_user(is_promoter=True))
        resp = client.get("/promoter/claim")
        assert b"You need a label" in resp.data

    def test_claim_assigns_label(
        self, client, make_user, make_label, make_event, login
    ):
        promoter = make_user(is_promoter=True)
        label = make_label(promoter)
        event = make_event()
        login(promoter)
        resp = client.post(
            f"/promoter/events/{event.id}/claim",
            data={"label_id": str(label.id)},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert b"Event claimed" in resp.data
        assert db.session.get(Event, event.id).label_id == label.id

    def test_cannot_claim_with_foreign_label(
        self, client, make_user, make_label, make_event, login
    ):
        owner = make_user(email="owner@example.com", is_promoter=True)
        foreign_label = make_label(owner)
        event = make_event()
        login(make_user(email="other@example.com", is_promoter=True))
        resp = client.post(
            f"/promoter/events/{event.id}/claim",
            data={"label_id": str(foreign_label.id)},
        )
        assert resp.status_code == 400
        assert db.session.get(Event, event.id).label_id is None

    def test_cannot_claim_already_labelled_event(
        self, client, make_user, make_label, make_event, login
    ):
        owner = make_user(email="owner@example.com", is_promoter=True)
        their_label = make_label(owner, name="Theirs")
        event = make_event(label_id=their_label.id)
        me = make_user(email="me@example.com", is_promoter=True)
        my_label = make_label(me, name="Mine")
        login(me)
        resp = client.post(
            f"/promoter/events/{event.id}/claim",
            data={"label_id": str(my_label.id)},
            follow_redirects=True,
        )
        assert b"already belongs to a label" in resp.data
        assert db.session.get(Event, event.id).label_id == their_label.id

    def test_admin_may_claim_with_any_label(
        self, client, make_user, make_label, make_event, admin, login
    ):
        label = make_label(make_user(is_promoter=True))
        event = make_event()
        login(admin)
        client.post(
            f"/promoter/events/{event.id}/claim", data={"label_id": str(label.id)}
        )
        assert db.session.get(Event, event.id).label_id == label.id


class TestDashboard:
    def test_shows_view_counts_for_label_events(
        self, client, make_user, make_label, make_event, login
    ):
        promoter = make_user(is_promoter=True)
        label = make_label(promoter)
        event = make_event(name="Tracked Show", label_id=label.id)
        yesterday = date.today() - timedelta(days=1)
        db.session.add_all(
            [
                EventDailyViews(event_id=event.id, date=yesterday, hits=7, visitors=3),
                EventDailyViews(
                    event_id=event.id, date=date.today(), hits=5, visitors=2
                ),
            ]
        )
        db.session.commit()
        login(promoter)
        resp = client.get("/promoter/")
        assert b"Tracked Show" in resp.data
        assert b"12" in resp.data  # summed hits
        assert f"/events/{event.id}/".encode() in resp.data

    def test_includes_events_submitted_by_others(
        self, client, make_user, make_label, make_event, login
    ):
        promoter = make_user(email="promo@example.com", is_promoter=True)
        other = make_user(email="other@example.com")
        label = make_label(promoter)
        make_event(name="Foreign Submission", label_id=label.id, submitter_id=other.id)
        login(promoter)
        assert b"Foreign Submission" in client.get("/promoter/").data

    def test_hides_other_promoters_labels(
        self, client, make_user, make_label, make_event, login
    ):
        owner = make_user(email="owner@example.com", is_promoter=True)
        label = make_label(owner, name="Theirs")
        make_event(name="Their Show", label_id=label.id)
        login(make_user(email="me@example.com", is_promoter=True))
        resp = client.get("/promoter/")
        assert b"Their Show" not in resp.data
        assert b"Theirs" not in resp.data
