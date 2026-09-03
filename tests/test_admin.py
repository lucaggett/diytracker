"""Admin dashboard, event/venue management, and exports."""

from diytracker.models import Event, Venue, db


def _force_dangling_venue(event_id):
    """Point an event at a venue id that doesn't exist. FK enforcement
    rejects this through the ORM, so drop to a raw connection with FKs off —
    the admin views must still tolerate legacy rows corrupted before
    enforcement was turned on."""
    with db.engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
        conn.exec_driver_sql("UPDATE event SET venue_id=999999 WHERE id=?", (event_id,))
        conn.commit()
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")
    db.session.expire_all()


class TestEventManagement:
    def test_edit_event_updates_fields(self, client, admin, login, make_event):
        login(admin)
        ev = make_event(name="Before")
        resp = client.post(
            f"/admin/edit_event/{ev.id}",
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

    def test_edit_event_sets_status(self, client, admin, login, make_event):
        login(admin)
        ev = make_event()
        assert ev.status == "scheduled"
        resp = client.post(
            f"/admin/edit_event/{ev.id}",
            data={
                "name": ev.name,
                "date": ev.date.strftime("%Y-%m-%d"),
                "doors": "19:00",
                "acts": ev.acts,
                "ticket_price": ev.ticket_price,
                "venue_id": str(ev.venue_id),
                "status": "cancelled",
            },
        )
        assert resp.status_code == 302
        db.session.refresh(ev)
        assert ev.status == "cancelled"

    def test_edit_requires_admin(self, client, make_user, login, make_event):
        ev = make_event()
        login(make_user(is_admin=False))
        assert client.get(f"/admin/edit_event/{ev.id}").status_code == 403

    def test_delete_event(self, client, admin, login, make_event):
        login(admin)
        ev = make_event()
        resp = client.post(f"/admin/delete_event/{ev.id}")
        assert resp.status_code == 302
        assert Event.query.get(ev.id) is None

    def test_edit_event_with_dangling_venue_id(self, client, admin, login, make_event):
        # Simulate an event whose venue was deleted out from under it before
        # FK enforcement existed — the edit page must not crash.
        login(admin)
        ev = make_event()
        _force_dangling_venue(ev.id)
        resp = client.get(f"/admin/edit_event/{ev.id}")
        assert resp.status_code == 200

    def test_edit_missing_event_404(self, client, admin, login):
        login(admin)
        assert client.get("/admin/edit_event/9999").status_code == 404

    def test_admin_dashboard_flags_events_with_missing_venue(
        self, client, admin, login, make_event
    ):
        login(admin)
        ev = make_event()
        _force_dangling_venue(ev.id)
        resp = client.get("/admin")
        assert resp.status_code == 200
        assert b"Events with missing venue" in resp.data
        assert ev.name.encode() in resp.data

    def test_admin_dashboard_lists_the_venue_name(
        self, client, admin, login, make_venue, make_event
    ):
        # The table used to read `event.venue_name`, which Event does not
        # have — Jinja rendered the column blank instead of raising.
        login(admin)
        make_event(venue=make_venue(name="Rote Fabrik"))
        resp = client.get("/admin")
        assert resp.status_code == 200
        assert b"Rote Fabrik" in resp.data

    def test_admin_dashboard_survives_a_dangling_venue(
        self, client, admin, login, make_event
    ):
        login(admin)
        ev = make_event()
        _force_dangling_venue(ev.id)
        assert client.get("/admin").status_code == 200

    def test_admin_dashboard_hides_missing_venue_section_when_clean(
        self, client, admin, login, make_event
    ):
        login(admin)
        make_event()
        resp = client.get("/admin")
        assert resp.status_code == 200
        assert b"Events with missing venue" not in resp.data

    def test_edit_event_legacy_url_redirects(self, client, admin, login, make_event):
        login(admin)
        ev = make_event()
        resp = client.get(f"/edit_event/{ev.id}")
        assert resp.status_code == 301
        assert resp.headers["Location"].endswith(f"/admin/edit_event/{ev.id}")


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

    def test_edit_venue_busts_the_page_cache(
        self, client, admin, login, make_venue, make_event
    ):
        from diytracker.services.cantons import canton_directory

        login(admin)
        v = make_venue(name="Rote Fabrik", city="Zürich", canton="ZH")
        make_event(venue=v)
        assert "Zürich" in {i["name"] for i in canton_directory().values()}

        client.post(
            f"/admin/venues/{v.id}/edit",
            data={
                "name": "Rote Fabrik",
                "city": "Basel",
                "plz": "4000",
                "canton": "BS",
            },
        )
        assert "Basel-Stadt" in {i["name"] for i in canton_directory().values()}

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


class TestAnalyticsStats:
    def test_requires_admin(self, client, make_user, login):
        login(make_user(is_admin=False))
        assert client.get("/admin/analytics/stats").status_code == 403

    def test_pending_when_no_cache(self, client, admin, login, tmp_path, monkeypatch):
        from diytracker.blueprints import admin as admin_bp
        from diytracker.services import analytics

        login(admin)
        monkeypatch.setattr(analytics, "STATS_PATH", tmp_path / "stats.json")
        kicked = []
        monkeypatch.setattr(admin_bp, "kick_stats_generation", kicked.append)
        resp = client.get("/admin/analytics/stats")
        assert resp.status_code == 202
        assert resp.get_json() == {"status": "pending"}
        assert len(kicked) == 1

    def test_serves_cached_stats(self, client, admin, login, tmp_path, monkeypatch):
        import json

        from diytracker.blueprints import admin as admin_bp
        from diytracker.services import analytics

        login(admin)
        stats_path = tmp_path / "stats.json"
        stats_path.write_text(
            json.dumps(
                {
                    "generated_at": "2026-07-13T12:00:00",
                    "window_days": 30,
                    "totals": {"today": {"hits": 1, "visitors": 1}},
                    "growth_30d": {},
                    "daily": [],
                }
            )
        )
        monkeypatch.setattr(analytics, "STATS_PATH", stats_path)
        kicked = []
        monkeypatch.setattr(admin_bp, "kick_stats_generation", kicked.append)
        resp = client.get("/admin/analytics/stats")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert data["totals"]["today"] == {"hits": 1, "visitors": 1}
        assert data["age_seconds"] < 60
        # Fresh cache -> no background regeneration kicked.
        assert kicked == []


class TestStatisticsPage:
    def test_requires_admin(self, client):
        resp = client.get("/admin/statistics")
        assert resp.status_code == 302

    def test_renders_sections(self, client, admin, login, make_event):
        login(admin)
        make_event(name="Some Show", ticket_price="15.-")
        resp = client.get("/admin/statistics")
        assert resp.status_code == 200
        assert b"Most viewed events" in resp.data
        assert b"Events over time" in resp.data
        assert b"By genre" in resp.data
        assert b"By canton" in resp.data
        assert b"Top venues" in resp.data
        assert b"Ticket prices" in resp.data
        # No view data yet — the popularity table shows its empty state.
        assert b"No upcoming events with view data yet." in resp.data

    def test_shows_popularity_rows(self, client, admin, login, make_event):
        from datetime import date

        from diytracker.services.event_views import persist_event_day_counts

        login(admin)
        ev = make_event(name="Viewed Show", days_from_now=5)
        persist_event_day_counts({(ev.id, date(2026, 7, 1)): (12, 7)})
        resp = client.get("/admin/statistics")
        assert b"Viewed Show" in resp.data
        # The test client browses in English (Accept-Language), so public
        # links carry the locale prefix.
        assert f'href="/en/events/{ev.id}/"'.encode() in resp.data

    def test_past_events_only_with_toggle(self, client, admin, login, make_event):
        from datetime import date

        from diytracker.services.event_views import persist_event_day_counts

        login(admin)
        ev = make_event(name="Bygone Show", days_from_now=-5)
        persist_event_day_counts({(ev.id, date(2026, 7, 1)): (12, 7)})

        resp = client.get("/admin/statistics")
        assert b"Bygone Show" not in resp.data
        assert b'aria-checked="false"' in resp.data

        resp = client.get("/admin/statistics?past=1")
        assert b"Bygone Show" in resp.data
        assert b'aria-checked="true"' in resp.data


class TestEditEventLabel:
    def test_edit_event_assigns_and_clears_label(
        self, client, admin, login, make_event, make_user, make_label
    ):
        label = make_label(make_user(email="promo@example.com", is_promoter=True))
        login(admin)
        ev = make_event()
        base = {
            "name": ev.name,
            "date": ev.date.strftime("%Y-%m-%d"),
            "doors": "19:00",
            "acts": ev.acts,
            "ticket_price": ev.ticket_price,
            "venue_id": str(ev.venue_id),
        }
        client.post(
            f"/admin/edit_event/{ev.id}", data={**base, "label_id": str(label.id)}
        )
        db.session.refresh(ev)
        assert ev.label_id == label.id
        client.post(f"/admin/edit_event/{ev.id}", data={**base, "label_id": ""})
        db.session.refresh(ev)
        assert ev.label_id is None
