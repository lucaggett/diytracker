"""Promoter accounts, labels and the promoter dashboard."""

import io
from datetime import date, timedelta

from PIL import Image

from diytracker.models import Event, EventDailyViews, Label, db


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

    def test_admin_may_not_edit_others_label(
        self, client, make_user, make_label, admin, login
    ):
        label = make_label(make_user(is_promoter=True))
        login(admin)
        assert client.get(f"/promoter/labels/{label.id}/edit").status_code == 403

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

    def test_admin_may_not_claim_with_others_label(
        self, client, make_user, make_label, make_event, admin, login
    ):
        label = make_label(make_user(is_promoter=True))
        event = make_event()
        login(admin)
        resp = client.post(
            f"/promoter/events/{event.id}/claim", data={"label_id": str(label.id)}
        )
        assert resp.status_code == 400
        assert db.session.get(Event, event.id).label_id is None


class TestDashboardTutorial:
    def test_tutorial_starts_open_without_labels(self, client, make_user, login):
        login(make_user(is_promoter=True))
        html = client.get("/promoter/").data.decode()
        assert "How this page works" in html
        assert '<details class="border-2 border-ink bg-paper p-4 mb-6" open>' in html

    def test_tutorial_collapses_once_a_label_exists(
        self, client, make_user, make_label, login
    ):
        promoter = make_user(is_promoter=True)
        make_label(promoter)
        login(promoter)
        html = client.get("/promoter/").data.decode()
        assert "How this page works" in html
        assert 'mb-6" open>' not in html

    def test_links_to_the_help_page(self, client, make_user, login):
        login(make_user(is_promoter=True))
        html = client.get("/promoter/").data.decode()
        # The help route is locale-prefixed, so match on the tail only.
        assert html.count('help#labels"') == 2  # header link + full guide


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

    def test_admin_dashboard_hides_others_labels(
        self, client, make_user, make_label, make_event, admin, login
    ):
        label = make_label(make_user(is_promoter=True), name="Theirs")
        make_event(name="Their Show", label_id=label.id)
        login(admin)
        resp = client.get("/promoter/")
        assert b"Their Show" not in resp.data
        assert b"Theirs" not in resp.data


class TestClaimSearch:
    def test_search_narrows_results(
        self, client, make_user, make_label, make_venue, make_event, login
    ):
        promoter = make_user(is_promoter=True)
        make_label(promoter)
        bern = make_venue(name="Reitschule", city="Bern", plz="3000", canton="BE")
        make_event(name="Punk Night", venue=bern)
        make_event(name="Metal Fest")
        login(promoter)
        resp = client.get("/promoter/claim?q=punk+bern")
        assert b"Punk Night" in resp.data
        assert b"Metal Fest" not in resp.data
        resp = client.get("/promoter/claim?q=zzzznothing")
        assert b"No events match your search." in resp.data

    def test_pagination(self, client, make_user, make_label, make_event, login):
        from diytracker.blueprints.promoter import CLAIM_PAGE_SIZE

        promoter = make_user(is_promoter=True)
        make_label(promoter)
        for i in range(CLAIM_PAGE_SIZE + 1):
            make_event(name=f"Bulk Show {i:03d}", days_from_now=10 + i % 5)
        login(promoter)
        page1 = client.get("/promoter/claim")
        page2 = client.get("/promoter/claim?page=2")
        assert page1.data.count(b"Bulk Show") <= CLAIM_PAGE_SIZE * 2  # suggestions off
        assert b"Bulk Show" in page2.data
        assert b"Page 1 of 2" in page1.data

    def test_likely_yours_suggestion(
        self, client, make_user, make_label, make_event, login
    ):
        promoter = make_user(is_promoter=True)
        make_label(promoter, name="Kälte Kollektiv")
        make_event(name="Kaelte Kollektiv Fest")
        make_event(name="Unrelated Show")
        login(promoter)
        resp = client.get("/promoter/claim")
        assert b"Likely yours" in resp.data
        assert b"Kaelte Kollektiv Fest" in resp.data

    def test_no_suggestions_section_without_match(
        self, client, make_user, make_label, make_event, login
    ):
        promoter = make_user(is_promoter=True)
        make_label(promoter, name="Cool Label")
        make_event(name="Unrelated Show")
        login(promoter)
        assert b"Likely yours" not in client.get("/promoter/claim").data


class TestUnclaim:
    def test_owner_can_unclaim_and_it_logs(
        self, client, make_user, make_label, make_event, login
    ):
        from diytracker.models import ActionLog

        promoter = make_user(is_promoter=True)
        label = make_label(promoter)
        event = make_event(name="Claimed Show", label_id=label.id)
        login(promoter)
        resp = client.post(f"/promoter/events/{event.id}/unclaim")
        assert resp.status_code == 302
        db.session.refresh(event)
        assert event.label_id is None
        row = ActionLog.query.filter_by(action="label.unclaim").one()
        assert row.target_id == event.id
        assert row.actor == promoter.email

    def test_non_owner_cannot_unclaim(
        self, client, make_user, make_label, make_event, login
    ):
        label = make_label(make_user(email="owner@example.com", is_promoter=True))
        event = make_event(label_id=label.id)
        login(make_user(email="other@example.com", is_promoter=True))
        assert client.post(f"/promoter/events/{event.id}/unclaim").status_code == 403
        db.session.refresh(event)
        assert event.label_id == label.id

    def test_admin_cannot_unclaim_others(
        self, client, make_user, make_label, make_event, admin, login
    ):
        label = make_label(make_user(is_promoter=True))
        event = make_event(label_id=label.id)
        login(admin)
        assert client.post(f"/promoter/events/{event.id}/unclaim").status_code == 403

    def test_unlabelled_event_404s_or_403s(
        self, client, make_user, make_label, make_event, login
    ):
        promoter = make_user(is_promoter=True)
        make_label(promoter)
        event = make_event()
        login(promoter)
        assert client.post(f"/promoter/events/{event.id}/unclaim").status_code == 403


class TestLabelProfile:
    def _base_data(self, name="Profile Label", **extra):
        data = {"name": name}
        data.update(extra)
        return data

    def test_profile_fields_round_trip(self, client, make_user, login):
        login(make_user(is_promoter=True))
        client.post(
            "/promoter/labels/new",
            data=self._base_data(
                website="https://example.org",
                link_social_1="https://instagram.com/x",
                link_social_2="https://x.bandcamp.com",
                contact_email="booking@example.org",
            ),
        )
        label = Label.query.filter_by(name="Profile Label").one()
        assert label.website == "https://example.org"
        assert label.link_social_1 == "https://instagram.com/x"
        assert label.link_social_2 == "https://x.bandcamp.com"
        assert label.contact_email == "booking@example.org"

    def test_unsafe_website_rejected(self, client, make_user, login):
        login(make_user(is_promoter=True))
        resp = client.post(
            "/promoter/labels/new",
            data=self._base_data(website="javascript:alert(1)"),
        )
        assert resp.status_code == 200  # re-rendered with errors
        assert Label.query.count() == 0

    def test_bad_contact_email_rejected(self, client, make_user, login):
        login(make_user(is_promoter=True))
        client.post(
            "/promoter/labels/new", data=self._base_data(contact_email="not-an-email")
        )
        assert Label.query.count() == 0

    def test_remove_logo_clears_column(self, client, make_user, make_label, login):
        promoter = make_user(is_promoter=True)
        label = make_label(promoter, logo="uploads/logo.png")
        login(promoter)
        client.post(
            f"/promoter/labels/{label.id}/edit",
            data=self._base_data(name=label.name, remove_logo="y"),
            content_type="multipart/form-data",
        )
        db.session.refresh(label)
        assert label.logo is None

    def test_upload_beats_remove_checkbox(self, client, make_user, make_label, login):
        promoter = make_user(is_promoter=True)
        label = make_label(promoter, logo="uploads/old.png")
        login(promoter)
        client.post(
            f"/promoter/labels/{label.id}/edit",
            data=self._base_data(
                name=label.name, remove_logo="y", logo=_png_upload("new.png")
            ),
            content_type="multipart/form-data",
        )
        db.session.refresh(label)
        assert label.logo is not None
        assert "old.png" not in label.logo

    def test_public_label_page_shows_profile(self, client, make_user, make_label):
        label = make_label(
            make_user(is_promoter=True),
            name="Linked Label",
            website="https://example.org/",
            contact_email="booking@example.org",
            description="Kollektiv. Mehr auf https://blog.example.org und so.",
        )
        html = client.get(f"/label/{label.slug}/").data.decode()
        assert 'href="https://example.org/"' in html
        assert "example.org" in html
        assert 'href="mailto:booking@example.org"' in html
        assert 'href="https://blog.example.org"' in html

    def test_unsafe_stored_links_not_rendered(self, client, make_user, make_label):
        # Defense in depth: even a value that bypassed form validation must
        # not render as a link.
        label = make_label(
            make_user(is_promoter=True),
            name="Evil Label",
            website="javascript:alert(1)",
            description="javascript:alert(2)",
        )
        html = client.get(f"/label/{label.slug}/").data.decode()
        assert 'href="javascript:' not in html


class TestLabelSuggestionActSplitting:
    def test_matches_a_newline_separated_lineup(
        self, client, make_user, login, make_label, make_event
    ):
        """Suggestions split the line-up on commas only, so a newline- or
        pipe-separated one compared as a single long string and a label whose
        name matched one act in it never scored. seo.parse_acts is what the
        event page, the JSON-LD and the ICS export all use.
        """
        from diytracker.services.labels import suggest_label_events

        user = make_user(is_promoter=True)
        login(user)
        label = make_label(user, name="Rat Poison Records")
        make_event(name="Basement Show", acts="Some Band\nRat Poison Records\nAnother")

        from diytracker.models import Event
        from diytracker.services.events import upcoming_filter

        base = Event.query.filter(Event.label_id.is_(None), upcoming_filter())
        suggestions = suggest_label_events([label], base)

        assert [(e.name, lb.name) for e, lb in suggestions] == [
            ("Basement Show", "Rat Poison Records")
        ]
