"""Admin dashboard, event/venue management, and exports."""
from datetime import datetime, timedelta

from models import db, Event, Venue, VenueAccessibility


class TestEventManagement:
    def test_edit_event_updates_fields(self, client, admin, login, make_event):
        login(admin)
        ev = make_event(name='Before')
        resp = client.post(f'/edit_event/{ev.id}', data={
            'name': 'After',
            'date': ev.date.strftime('%Y-%m-%d'),
            'doors': '22:00',
            'genre': ['Techno'],
            'acts': 'DJ',
            'ticket_price': '25',
            'venue_id': str(ev.venue_id),
        })
        assert resp.status_code == 302
        db.session.refresh(ev)
        assert ev.name == 'After'
        assert ev.genre == 'Techno'
        assert ev.parent_genres == ',Electronic,'

    def test_edit_requires_admin(self, client, make_user, login, make_event):
        ev = make_event()
        login(make_user(is_admin=False))
        assert client.get(f'/edit_event/{ev.id}').status_code == 403

    def test_delete_event(self, client, admin, login, make_event):
        login(admin)
        ev = make_event()
        resp = client.post(f'/delete_event/{ev.id}')
        assert resp.status_code == 302
        assert Event.query.get(ev.id) is None

    def test_edit_missing_event_404(self, client, admin, login):
        login(admin)
        assert client.get('/edit_event/9999').status_code == 404


class TestVenueManagement:
    def test_venue_list(self, client, admin, login, make_venue):
        login(admin)
        make_venue(name='Rote Fabrik')
        resp = client.get('/admin/venues')
        assert resp.status_code == 200
        assert b'Rote Fabrik' in resp.data

    def test_edit_venue(self, client, admin, login, make_venue):
        login(admin)
        v = make_venue(name='Old Name')
        resp = client.post(f'/admin/venues/{v.id}/edit', data={
            'name': 'New Name', 'city': 'Basel', 'plz': '4000', 'canton': 'BS',
        })
        assert resp.status_code == 302
        db.session.refresh(v)
        assert v.name == 'New Name'
        assert v.city == 'Basel'

    def test_delete_empty_venue(self, client, admin, login, make_venue):
        login(admin)
        v = make_venue()
        resp = client.post(f'/admin/venues/{v.id}/delete')
        assert resp.status_code == 302
        assert Venue.query.get(v.id) is None

    def test_cannot_delete_venue_with_events(self, client, admin, login, make_event):
        login(admin)
        ev = make_event()
        venue_id = ev.venue_id
        resp = client.post(f'/admin/venues/{venue_id}/delete', follow_redirects=True)
        assert b'Cannot delete venue' in resp.data
        assert Venue.query.get(venue_id) is not None

    def test_generate_accessibility_link(self, client, admin, login, make_venue):
        login(admin)
        v = make_venue()
        resp = client.get(f'/admin/venues/{v.id}/accessibility-link')
        assert resp.status_code == 302
        db.session.refresh(v)
        assert v.accessibility_token is not None


class TestExportsAndStatus:
    def test_excel_export(self, client, admin, login, make_event):
        login(admin)
        make_event()
        resp = client.get('/admin/export-excel')
        assert resp.status_code == 200
        assert 'spreadsheetml' in resp.headers['Content-Type']
        assert resp.headers['Content-Disposition'].startswith('attachment')
        # XLSX is a zip archive -> starts with PK.
        assert resp.data[:2] == b'PK'

    def test_scrape_status_idle(self, client, admin, login):
        login(admin)
        resp = client.get('/admin/scrape-status')
        assert resp.get_json() == {'running': False}

    def test_export_requires_admin(self, client, make_user, login):
        login(make_user(is_admin=False))
        assert client.get('/admin/export-excel').status_code == 403
