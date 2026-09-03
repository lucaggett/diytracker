"""Event submission & scrape-queue approval flow (the app's core workflow)."""

from datetime import date, datetime, time, timedelta

from diytracker.models import Event, Genre, ScrapedEvent, Venue, db


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
        defaults = {
            "source": "metalgigs",
            "url": "https://metalgigs.ch/konzerte/show-1",
            "title": "Scraped Show",
            "performers": "Headliner",
            "styles": "Metal",
            "start_date": date.today() + timedelta(days=20),
            "doors_open": time(19, 0),
            "venue_name": "Hall",
            "city": "Aarau",
            "postal_code": "5000",
            "region": "AG",
            "approved": False,
        }
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

    def test_flagged_duplicate_hidden_from_queue(self, client, admin, login):
        login(admin)
        self._make_scraped(title="Clean Row")
        self._make_scraped(
            title="Flagged Dup",
            url="https://metalgigs.ch/konzerte/dup",
            needs_review=True,
            review_reason="Calendar: Clean Row @ Aarau",
        )
        resp = client.get("/queue")
        assert b"Clean Row" in resp.data
        assert b"Flagged Dup" not in resp.data

    def test_approving_creates_event_and_marks_scraped(self, client, admin, login):
        login(admin)
        rec = self._make_scraped()
        resp = client.post("/queue", data={"scraped_id": str(rec.id)})
        assert resp.status_code == 302

        ev = Event.query.one()
        assert ev.name == "Scraped Show"
        assert ev.venue.name == "Hall"

        db.session.refresh(rec)
        assert rec.status == ScrapedEvent.STATUS_PUBLISHED
        assert rec.approved_event_id == ev.id

    def test_duplicate_event_is_rejected_by_hash(self, client, admin, login):
        login(admin)
        # Two distinct scraped rows that resolve to identical event content
        # (same venue + name + date + …) -> identical event_hash.
        common = {
            "title": "Same Gig",
            "styles": "Punk",
            "venue_name": "Hall",
            "city": "Aarau",
            "postal_code": "5000",
            "region": "AG",
            "start_date": date.today() + timedelta(days=20),
            "doors_open": time(19, 0),
            "performers": "Headliner",
        }
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
        rec = self._make_scraped(approved=True, status="published")
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

    def test_delete_from_queue_marks_rejected(self, client, admin, login):
        login(admin)
        rec = self._make_scraped()
        resp = client.post(f"/queue/{rec.id}/delete")
        assert resp.status_code == 302
        db.session.refresh(rec)
        assert rec.status == ScrapedEvent.STATUS_REJECTED
        # No Event created by a delete.
        assert Event.query.count() == 0

    def test_queue_requires_login(self, client):
        resp = client.get("/queue")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]


class TestSubmitWithLabel:
    def test_submit_with_own_label_sets_label_id(
        self, client, make_user, make_label, make_venue, login
    ):
        promoter = make_user(email="promo@example.com", is_promoter=True)
        label = make_label(promoter)
        login(promoter)
        venue = make_venue()
        client.post(
            "/submit",
            data={
                "name": "Labelled Show",
                "date": _future(),
                "doors": "20:00",
                "ticket_price": "15",
                "venue_id": str(venue.id),
                "label_id": str(label.id),
            },
        )
        ev = Event.query.filter_by(name="Labelled Show").one()
        assert ev.label_id == label.id

    def test_submit_with_foreign_label_is_rejected(
        self, client, make_user, make_label, make_venue, login
    ):
        label = make_label(make_user(email="promo@example.com", is_promoter=True))
        login(make_user())
        venue = make_venue()
        client.post(
            "/submit",
            data={
                "name": "Hijacked Show",
                "date": _future(),
                "doors": "20:00",
                "ticket_price": "15",
                "venue_id": str(venue.id),
                "label_id": str(label.id),
            },
        )
        assert Event.query.filter_by(name="Hijacked Show").count() == 0

    def test_admin_cannot_submit_under_foreign_label(
        self, client, make_user, make_label, make_venue, admin, login
    ):
        label = make_label(make_user(email="promo@example.com", is_promoter=True))
        login(admin)
        venue = make_venue()
        client.post(
            "/submit",
            data={
                "name": "Admin Hijack",
                "date": _future(),
                "doors": "20:00",
                "ticket_price": "15",
                "venue_id": str(venue.id),
                "label_id": str(label.id),
            },
        )
        assert Event.query.filter_by(name="Admin Hijack").count() == 0

    def test_submit_without_label_leaves_it_null(
        self, client, make_user, make_venue, login
    ):
        login(make_user())
        venue = make_venue()
        client.post(
            "/submit",
            data={
                "name": "Plain Show",
                "date": _future(),
                "doors": "20:00",
                "ticket_price": "15",
                "venue_id": str(venue.id),
                "label_id": "",
            },
        )
        ev = Event.query.filter_by(name="Plain Show").one()
        assert ev.label_id is None


class TestManageGenres:
    def test_requires_login(self, client):
        assert client.get("/genres").status_code == 302

    def test_lists_seeded_genres(self, client, make_user, login):
        login(make_user())
        resp = client.get("/genres")
        assert resp.status_code == 200
        assert b"Crustpunk" in resp.data

    def test_old_suggest_url_redirects(self, client, make_user, login):
        login(make_user())
        resp = client.get("/genres/submit")
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/genres")

    def test_add_genre(self, client, make_user, login):
        user = login(make_user())
        resp = client.post("/genres", data={"name": "  Zeuhl  "}, follow_redirects=True)
        assert b"added" in resp.data
        genre = Genre.query.filter_by(name="Zeuhl").one()
        assert genre.added_by_id == user.id
        # Immediately available in the event form's autocomplete source.
        assert "Zeuhl" in client.get("/get_genres").get_json()["genres"]

    def test_add_duplicate_is_rejected_case_insensitively(
        self, client, make_user, login
    ):
        login(make_user())
        resp = client.post("/genres", data={"name": "crustpunk"}, follow_redirects=True)
        assert b"already in the list" in resp.data
        assert Genre.query.filter_by(name="crustpunk").first() is None

    def test_delete_own_genre(self, client, make_user, login):
        user = login(make_user())
        genre = Genre(name="Zeuhl", added_by_id=user.id)
        db.session.add(genre)
        db.session.commit()
        resp = client.post(f"/genres/{genre.id}/delete", follow_redirects=True)
        assert b"removed" in resp.data
        assert db.session.get(Genre, genre.id) is None

    def test_cannot_delete_other_users_genre(self, client, make_user, login):
        other = make_user(email="other@example.com")
        genre = Genre(name="Zeuhl", added_by_id=other.id)
        db.session.add(genre)
        db.session.commit()
        login(make_user(email="me@example.com"))
        assert client.post(f"/genres/{genre.id}/delete").status_code == 403
        assert db.session.get(Genre, genre.id) is not None

    def test_cannot_delete_seed_genre(self, client, make_user, login):
        login(make_user())
        genre = Genre.query.filter_by(name="Punk").one()  # seeded, added_by NULL
        assert client.post(f"/genres/{genre.id}/delete").status_code == 403

    def test_admin_can_delete_any_genre(self, client, make_user, admin, login):
        other = make_user(email="other@example.com")
        genre = Genre(name="Zeuhl", added_by_id=other.id)
        db.session.add(genre)
        db.session.commit()
        login(admin)
        client.post(f"/genres/{genre.id}/delete")
        assert db.session.get(Genre, genre.id) is None

    def test_delete_missing_genre_404s(self, client, make_user, login):
        login(make_user())
        assert client.post("/genres/99999/delete").status_code == 404


class TestQueueStatus:
    def _make_scraped(self, **kw):
        return TestEventQueue._make_scraped(TestEventQueue(), **kw)

    def test_approve_sets_published(self, client, admin, login):
        login(admin)
        rec = self._make_scraped()
        client.post("/queue", data={"scraped_id": str(rec.id)})
        db.session.refresh(rec)
        assert rec.status == "published"
        assert rec.approved_event_id is not None

    def test_reject_sets_rejected_with_reason(self, client, admin, login):
        login(admin)
        rec = self._make_scraped()
        client.post(f"/queue/{rec.id}/delete", data={"reject_reason": "wrong city"})
        db.session.refresh(rec)
        assert rec.status == "rejected"
        assert rec.reject_reason == "wrong city"
        assert rec.approved_event_id is None
        from diytracker.models import ActionLog

        row = ActionLog.query.filter_by(action="queue.reject").one()
        assert "wrong city" in row.detail

    def test_rejected_rows_leave_the_queue(self, client, admin, login):
        login(admin)
        rec = self._make_scraped(title="Gone Show")
        client.post(f"/queue/{rec.id}/delete")
        assert b"Gone Show" not in client.get("/queue").data

    def test_bulk_reject(self, client, admin, login):
        login(admin)
        recs = [
            self._make_scraped(url=f"https://metalgigs.ch/konzerte/bulk-{i}")
            for i in range(3)
        ]
        keep = self._make_scraped(url="https://metalgigs.ch/konzerte/keep")
        client.post(
            "/queue/bulk-reject",
            data={
                "scraped_ids": [str(r.id) for r in recs[:2]],
                "reject_reason": "spam",
            },
        )
        for rec in recs[:2]:
            db.session.refresh(rec)
            assert rec.status == "rejected"
            assert rec.reject_reason == "spam"
        db.session.refresh(keep)
        assert keep.status == "pending"

    def test_queue_search(self, client, admin, login):
        login(admin)
        self._make_scraped(title="Grind Night", city="Bern", url="https://x.ch/1")
        self._make_scraped(title="Jazz Eve", city="Basel", url="https://x.ch/2")
        resp = client.get("/queue?q=grind")
        assert b"Grind Night" in resp.data
        assert b"Jazz Eve" not in resp.data

    def test_queue_pagination(self, client, admin, login):
        from diytracker.blueprints.submissions import QUEUE_PAGE_SIZE

        login(admin)
        for i in range(QUEUE_PAGE_SIZE + 1):
            self._make_scraped(title=f"Paged {i:03d}", url=f"https://x.ch/p{i}")
        page1 = client.get("/queue")
        assert b"Page 1 of 2" in page1.data
        assert b"Paged" in client.get("/queue?page=2").data


class TestQueueDuplicatesWeb:
    def _flagged(self, **kw):
        defaults = {"needs_review": True, "review_reason": "Calendar: X @ Bern"}
        defaults.update(kw)
        return TestEventQueue._make_scraped(TestEventQueue(), **defaults)

    def test_requires_admin(self, client, make_user, login):
        login(make_user())
        assert client.get("/queue/duplicates").status_code == 403

    def test_lists_flagged_rows(self, client, admin, login):
        login(admin)
        self._flagged(title="Dup Show")
        resp = client.get("/queue/duplicates")
        assert b"Dup Show" in resp.data
        assert b"Calendar: X @ Bern" in resp.data

    def test_discard_rejects_row(self, client, admin, login):
        login(admin)
        rec = self._flagged()
        client.post(f"/queue/duplicates/{rec.id}/discard")
        db.session.refresh(rec)
        assert rec.status == "rejected"

    def test_unflag_returns_row_to_queue(self, client, admin, login):
        login(admin)
        rec = self._flagged(title="False Alarm")
        client.post(f"/queue/duplicates/{rec.id}/unflag")
        db.session.refresh(rec)
        assert rec.needs_review is False
        assert rec.status == "pending"
        assert b"False Alarm" in client.get("/queue").data

    def test_flagged_rows_link_from_queue(self, client, admin, login):
        login(admin)
        self._flagged()
        resp = client.get("/queue")
        assert b"/queue/duplicates" in resp.data

    def test_calendar_collision_links_to_the_event_page(
        self, client, admin, login, make_venue, make_event
    ):
        """The whole point of the page: open the show it supposedly duplicates."""
        login(admin)
        venue = make_venue(name="Hall", city="Aarau", plz="5000", canton="AG")
        event = make_event(name="Scraped Show", venue=venue, days_from_now=20)
        self._flagged(title="Scraped Show")
        resp = client.get("/queue/duplicates")
        assert f"/events/{event.id}/".encode() in resp.data
        assert b"On the calendar" in resp.data

    def test_queue_collision_has_no_public_link(self, client, admin, login):
        """A colliding queue row isn't published, so it gets no /events/ link."""
        login(admin)
        TestEventQueue._make_scraped(
            TestEventQueue(), title="Scraped Show", url="https://x.ch/other"
        )
        self._flagged(title="Scraped Show")
        resp = client.get("/queue/duplicates")
        assert b"In the queue" in resp.data
        assert b"/events/" not in resp.data

    def test_stale_reason_shown_when_nothing_collides(self, client, admin, login):
        """review_reason is a snapshot; when the collision is gone, say so."""
        login(admin)
        self._flagged(title="Orphaned Flag")
        resp = client.get("/queue/duplicates")
        assert b"No longer collides" in resp.data
        assert b"Calendar: X @ Bern" in resp.data

    def test_bulk_discard_only_touches_checked_rows(self, client, admin, login):
        login(admin)
        keep = self._flagged(title="Keep", url="https://x.ch/keep")
        drop = self._flagged(title="Drop", url="https://x.ch/drop")
        client.post(
            "/queue/duplicates/bulk/discard", data={"scraped_ids": [str(drop.id)]}
        )
        db.session.refresh(keep)
        db.session.refresh(drop)
        assert drop.status == "rejected"
        assert keep.status == "pending"

    def test_bulk_unflag_returns_rows_to_the_queue(self, client, admin, login):
        login(admin)
        rec = self._flagged(title="Batch False Alarm")
        client.post(
            "/queue/duplicates/bulk/unflag", data={"scraped_ids": [str(rec.id)]}
        )
        db.session.refresh(rec)
        assert rec.needs_review is False
        assert b"Batch False Alarm" in client.get("/queue").data

    def test_bulk_skips_stale_ids(self, client, admin, login):
        """The checkboxes come from a page that may have been open a while."""
        login(admin)
        rec = self._flagged()
        resp = client.post(
            "/queue/duplicates/bulk/discard",
            data={"scraped_ids": [str(rec.id), "99999"]},
        )
        assert resp.status_code == 302
        db.session.refresh(rec)
        assert rec.status == "rejected"

    def test_bulk_rejects_unknown_action(self, client, admin, login):
        login(admin)
        assert client.post("/queue/duplicates/bulk/publish").status_code == 400

    def test_bulk_requires_admin(self, client, make_user, login):
        login(make_user())
        assert client.post("/queue/duplicates/bulk/discard").status_code == 403
