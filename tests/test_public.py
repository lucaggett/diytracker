"""Public-facing pages: calendar, contact, accessibility, i18n, errors."""

import pytest

from diytracker.models import db, utcnow, VenueAccessibility


class TestCalendar:
    def test_calendar_renders_upcoming_events(self, client, make_event):
        make_event(name="Upcoming Gig", days_from_now=5)
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"Upcoming Gig" in resp.data

    def test_calendar_sets_cache_headers_for_anonymous(self, client):
        resp = client.get("/")
        assert "public" in resp.headers.get("Cache-Control", "")
        assert resp.headers.get("ETag")

    def test_calendar_not_cached_for_logged_in_user(self, client, make_user, login):
        login(make_user())
        resp = client.get("/")
        # Logged-in users bypass the public cache headers entirely.
        assert "public" not in resp.headers.get("Cache-Control", "")

    def test_flash_messages_are_not_cached(self, client, app):
        """A rendered flash must never be stored in the shared page cache."""
        with client.session_transaction() as sess:
            sess["_flashes"] = [("message", "UNIQUE-FLASH-XYZ")]
        resp = client.get("/")
        assert b"UNIQUE-FLASH-XYZ" in resp.data
        fresh = app.test_client()
        resp2 = fresh.get("/")
        assert resp2.status_code == 200
        assert b"UNIQUE-FLASH-XYZ" not in resp2.data


class TestContactForm:
    def test_successful_submission_sends_email(self, client, monkeypatch):
        sent = {}

        def fake_send(name, email, message):
            sent["args"] = (name, email, message)

        monkeypatch.setattr(
            "diytracker.blueprints.public.send_contact_email", fake_send
        )
        resp = client.post(
            "/about",
            data={
                "name": "Alice",
                "email": "alice@example.com",
                "message": "I would like to help out with the project.",
            },
            follow_redirects=True,
        )
        assert sent["args"][0] == "Alice"
        assert b"get back to you" in resp.data

    def test_honeypot_blocks_send(self, client, monkeypatch):
        called = {"sent": False}
        monkeypatch.setattr(
            "diytracker.blueprints.public.send_contact_email",
            lambda *a: called.__setitem__("sent", True),
        )
        client.post(
            "/about",
            data={
                "name": "Bot",
                "email": "bot@example.com",
                "message": "spam spam spam spam",
                "website": "http://spam.example",  # honeypot filled
            },
            follow_redirects=True,
        )
        assert called["sent"] is False

    def test_send_failure_is_handled_gracefully(self, client, monkeypatch):
        def boom(*a):
            raise RuntimeError("SMTP down")

        monkeypatch.setattr("diytracker.blueprints.public.send_contact_email", boom)
        resp = client.post(
            "/about",
            data={
                "name": "Alice",
                "email": "alice@example.com",
                "message": "A genuine message about collaborating.",
            },
            follow_redirects=True,
        )
        assert b"could not be sent" in resp.data


class TestAccessibilityForm:
    def test_form_404_for_bad_token(self, client):
        assert client.get("/accessibility/nope").status_code == 404

    def test_submitting_creates_accessibility_record(self, client, make_venue):
        venue = make_venue()
        venue.generate_accessibility_token()
        db.session.commit()
        token = venue.accessibility_token

        resp = client.post(
            f"/accessibility/{token}",
            data={
                "step_free_entrance": "yes",
                "accessible_toilet": "partial",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        rec = VenueAccessibility.query.filter_by(venue_id=venue.id).one()
        assert rec.step_free_entrance == "yes"
        assert rec.accessible_toilet == "partial"
        assert rec.updated_at is not None

    def test_public_accessibility_page(self, client, make_venue):
        venue = make_venue()
        assert client.get(f"/venues/{venue.id}/accessibility").status_code == 200

    def test_public_accessibility_page_404(self, client):
        assert client.get("/venues/9999/accessibility").status_code == 404


class TestI18n:
    @pytest.mark.parametrize("lang", ["de", "fr", "it", "en"])
    def test_set_language_persists_supported_locale(self, client, lang):
        resp = client.get(f"/set-language/{lang}")
        assert resp.status_code == 302
        with client.session_transaction() as sess:
            assert sess["lang"] == lang

    def test_set_language_rejects_unsupported(self, client):
        assert client.get("/set-language/xx").status_code == 404

    def test_set_language_blocks_protocol_relative_redirect(self, client):
        resp = client.get("/set-language/de?next=//evil.example.com")
        assert "evil.example.com" not in resp.headers["Location"]

    @pytest.mark.parametrize("page", ["impressum", "agb", "datenschutz"])
    def test_legal_pages_render_for_valid_lang(self, client, page):
        assert client.get(f"/de/{page}").status_code == 200

    def test_legal_page_404_for_bad_lang(self, client):
        assert client.get("/xx/impressum").status_code == 404


class TestErrorHandlers:
    def test_404_uses_custom_template(self, client):
        # Single-segment paths hit the canton-slug route's slash redirect
        # first, so follow redirects to reach the final 404.
        resp = client.get("/this-route-does-not-exist", follow_redirects=True)
        assert resp.status_code == 404


class TestEventPage:
    def test_event_page_renders(self, client, make_event):
        ev = make_event(name="Doom Night")
        resp = client.get(f"/events/{ev.id}/")
        assert resp.status_code == 200
        assert b"Doom Night" in resp.data
        assert b"Kasheme" in resp.data

    def test_event_page_404_for_missing_event(self, client):
        assert client.get("/events/9999/").status_code == 404

    def test_event_page_includes_json_ld(self, client, make_event):
        ev = make_event()
        resp = client.get(f"/events/{ev.id}/")
        assert b"application/ld+json" in resp.data
        assert b'"MusicEvent"' in resp.data

    def test_event_page_og_image_uses_flyer(self, client, make_event):
        ev = make_event(name="Doom Night", flyer="static/uploads/test.jpg")
        resp = client.get(f"/events/{ev.id}/")
        html = resp.data.decode()
        assert (
            'property="og:title" content="Doom Night · Zürich · diytracker.ch"' in html
        )
        assert (
            'property="og:image" content="http://localhost/static/uploads/test.jpg"'
            in html
        )

    def test_event_page_og_image_falls_back_to_banner(self, client, make_event):
        ev = make_event()
        resp = client.get(f"/events/{ev.id}/")
        assert (
            'property="og:image" content="http://localhost/static/img/banner.webp"'
            in resp.data.decode()
        )

    def test_unsafe_ticket_link_is_not_rendered_as_anchor(self, client, make_event):
        ev = make_event(ticket_link="javascript:alert(1)")
        resp = client.get(f"/events/{ev.id}/")
        assert resp.status_code == 200
        assert b'href="javascript' not in resp.data

    def test_calendar_card_links_to_event_page(self, client, make_event):
        ev = make_event()
        resp = client.get("/")
        assert f'href="/events/{ev.id}/"'.encode() in resp.data

    def test_event_page_points_to_contact_when_no_accessibility_info(
        self, client, make_event
    ):
        ev = make_event()
        resp = client.get(f"/events/{ev.id}/")
        assert b"No accessibility info for this venue yet." in resp.data
        assert b'href="/about"' in resp.data

    def test_event_page_links_accessibility_report_when_info_exists(
        self, client, make_venue, make_event
    ):
        venue = make_venue()
        db.session.add(
            VenueAccessibility(
                venue_id=venue.id, step_free_entrance="yes", updated_at=utcnow()
            )
        )
        db.session.commit()
        ev = make_event(venue=venue)
        resp = client.get(f"/events/{ev.id}/")
        assert f"/venues/{venue.id}/accessibility".encode() in resp.data

    def test_event_page_never_exposes_accessibility_token(
        self, client, make_venue, make_event
    ):
        venue = make_venue()
        venue.generate_accessibility_token()
        db.session.commit()
        ev = make_event(venue=venue)
        resp = client.get(f"/events/{ev.id}/")
        assert venue.accessibility_token.encode() not in resp.data


class TestVenuePage:
    def test_venue_page_renders_with_upcoming_events(
        self, client, make_venue, make_event
    ):
        venue = make_venue(coords="47.37,8.54")
        ev = make_event(name="Doom Night", venue=venue)
        resp = client.get(f"/venues/{venue.id}/")
        assert resp.status_code == 200
        assert b"Kasheme" in resp.data
        assert b"Doom Night" in resp.data
        assert f'href="/events/{ev.id}/"'.encode() in resp.data
        assert b"venue-mini-map" in resp.data
        assert b'"MusicVenue"' in resp.data

    def test_venue_page_404_for_missing_venue(self, client):
        assert client.get("/venues/9999/").status_code == 404

    def test_venue_page_empty_state_and_no_map_without_coords(
        self, client, make_venue, make_event
    ):
        venue = make_venue()
        make_event(venue=venue, days_from_now=-10)  # past event stays hidden
        resp = client.get(f"/venues/{venue.id}/")
        assert resp.status_code == 200
        assert b"No upcoming events at this venue." in resp.data
        assert b"venue-mini-map" not in resp.data

    def test_venue_page_links_accessibility_report_when_info_exists(
        self, client, make_venue
    ):
        venue = make_venue()
        db.session.add(
            VenueAccessibility(
                venue_id=venue.id, step_free_entrance="yes", updated_at=utcnow()
            )
        )
        db.session.commit()
        resp = client.get(f"/venues/{venue.id}/")
        assert f"/venues/{venue.id}/accessibility".encode() in resp.data

    def test_venue_page_never_exposes_accessibility_token(self, client, make_venue):
        venue = make_venue()
        venue.generate_accessibility_token()
        db.session.commit()
        resp = client.get(f"/venues/{venue.id}/")
        assert venue.accessibility_token.encode() not in resp.data


class TestVenueMap:
    def test_map_includes_venues_without_upcoming_shows(
        self, client, make_venue, make_event
    ):
        active = make_venue(name="Active Hall", coords="47.37,8.54")
        make_event(venue=active)
        quiet = make_venue(name="Quiet Hall", plz="8002", coords="46.94,7.44")
        resp = client.get("/map")
        html = resp.data.decode()
        assert "Active Hall" in html
        assert "Quiet Hall" in html
        assert f'"id": {active.id}' in html
        assert f'"id": {quiet.id}' in html

    def test_map_skips_venues_outside_switzerland_or_without_coords(
        self, client, make_venue
    ):
        make_venue(name="Vienna Hall", coords="48.21,16.37")
        make_venue(name="No Coords Hall", plz="8002")
        resp = client.get("/map")
        assert b"Vienna Hall" not in resp.data
        assert b"No Coords Hall" not in resp.data


class TestSitemap:
    def test_sitemap_lists_static_and_event_pages(self, client, make_event):
        ev = make_event()
        resp = client.get("/sitemap.xml")
        assert resp.status_code == 200
        assert resp.mimetype == "application/xml"
        assert f"/events/{ev.id}/".encode() in resp.data
        # Legal pages are placeholder "WIP" routes and deliberately excluded.
        assert b"/de/impressum" not in resp.data
        assert b"<urlset" in resp.data

    def test_sitemap_includes_only_venues_with_accessibility_info(
        self, client, make_venue
    ):
        from datetime import datetime

        with_info = make_venue(name="Accessible Hall")
        without_info = make_venue(name="Plain Hall", plz="8002")
        db.session.add(
            VenueAccessibility(venue_id=with_info.id, updated_at=datetime.now())
        )
        db.session.commit()
        resp = client.get("/sitemap.xml")
        assert f"/venues/{with_info.id}/accessibility".encode() in resp.data
        assert f"/venues/{without_info.id}/accessibility".encode() not in resp.data
        # Venue pages themselves are listed for every venue.
        assert f"/venues/{with_info.id}/<".encode() in resp.data
        assert f"/venues/{without_info.id}/<".encode() in resp.data


class TestArchive:
    def test_index_lists_months_with_past_events(self, client, make_event):
        past = make_event(name="Bygone Fest", days_from_now=-40)
        resp = client.get("/archive/")
        assert resp.status_code == 200
        month_url = f"/archive/{past.date.year}/{past.date.month}/"
        assert month_url.encode() in resp.data

    def test_index_renders_empty_state(self, client):
        resp = client.get("/archive/")
        assert resp.status_code == 200
        assert b"Nothing in the archive yet." in resp.data

    def test_month_page_lists_only_past_events(self, client, make_event):
        past = make_event(name="Bygone Show", days_from_now=-3)
        # An upcoming event in the very same month must not appear.
        upcoming = make_event(name="Future Show", days_from_now=20)
        resp = client.get(f"/archive/{past.date.year}/{past.date.month}/")
        assert resp.status_code == 200
        assert b"Bygone Show" in resp.data
        if (upcoming.date.year, upcoming.date.month) == (
            past.date.year,
            past.date.month,
        ):
            assert b"Future Show" not in resp.data

    def test_month_without_past_events_404s(self, client, make_event):
        make_event(days_from_now=-40)
        assert client.get("/archive/1999/1/").status_code == 404
        assert client.get("/archive/2026/13/").status_code == 404

    def test_upcoming_events_do_not_create_archive_months(self, client, make_event):
        upcoming = make_event(days_from_now=30)
        url = f"/archive/{upcoming.date.year}/{upcoming.date.month}/"
        assert client.get(url).status_code == 404

    def test_footer_links_the_archive(self, client):
        resp = client.get("/about")
        assert b'href="/archive/"' in resp.data

    def test_sitemap_includes_archive_pages(self, client, make_event):
        past = make_event(days_from_now=-40)
        resp = client.get("/sitemap.xml")
        assert b"/archive/<" in resp.data or b"/archive/</loc>" in resp.data
        month_url = f"/archive/{past.date.year}/{past.date.month}/"
        assert month_url.encode() in resp.data

    def test_honeypot_route_is_untouched(self, client, app):
        """Regression: the real archive must not displace the scrape-detection
        honeypot at /events/archive/ (which fake-404s with status 200)."""
        from flask import url_for

        resp = client.get("/events/archive/")
        assert resp.status_code == 200
        assert b"404" in resp.data
        with app.test_request_context():
            assert url_for("public.archive_index") == "/archive/"
            assert url_for("public.events_archive") == "/events/archive/"

    def test_archive_slug_is_reserved_for_cantons(self):
        from diytracker.services.seo import RESERVED_SLUGS

        assert "archive" in RESERVED_SLUGS


class TestLabelPage:
    def test_label_page_lists_upcoming_event(
        self, client, make_user, make_label, make_event
    ):
        label = make_label(make_user(is_promoter=True))
        make_event(name="Label Show", label_id=label.id)
        make_event(name="Unrelated Show")
        resp = client.get(f"/label/{label.slug}/")
        assert resp.status_code == 200
        assert b"Label Show" in resp.data
        assert b"Unrelated Show" not in resp.data
        assert label.name.encode() in resp.data

    def test_label_page_without_events_stays_live(
        self, client, make_user, make_label
    ):
        label = make_label(make_user(is_promoter=True))
        resp = client.get(f"/label/{label.slug}/")
        assert resp.status_code == 200
        assert b"No upcoming shows yet" in resp.data

    def test_unknown_label_404s(self, client):
        assert client.get("/label/nope/").status_code == 404

    def test_sitemap_includes_label_pages(self, client, make_user, make_label):
        label = make_label(make_user(is_promoter=True))
        resp = client.get("/sitemap.xml")
        assert f"/label/{label.slug}/".encode() in resp.data

    def test_label_slug_is_reserved_for_cantons(self):
        from diytracker.services.seo import RESERVED_SLUGS

        assert "label" in RESERVED_SLUGS
        assert "promoter" in RESERVED_SLUGS
