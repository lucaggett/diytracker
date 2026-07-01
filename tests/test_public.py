"""Public-facing pages: calendar, contact, accessibility, i18n, errors."""
from datetime import datetime

import pytest

from models import db, VenueAccessibility


class TestCalendar:
    def test_calendar_renders_upcoming_events(self, client, make_event):
        make_event(name='Upcoming Gig', days_from_now=5)
        resp = client.get('/')
        assert resp.status_code == 200
        assert b'Upcoming Gig' in resp.data

    def test_calendar_sets_cache_headers_for_anonymous(self, client):
        resp = client.get('/')
        assert 'public' in resp.headers.get('Cache-Control', '')
        assert resp.headers.get('ETag')

    def test_calendar_not_cached_for_logged_in_user(self, client, make_user, login):
        login(make_user())
        resp = client.get('/')
        # Logged-in users bypass the public cache headers entirely.
        assert 'public' not in resp.headers.get('Cache-Control', '')

    def test_flash_messages_are_not_cached(self, client, app):
        """A rendered flash must never be stored in the shared page cache."""
        with client.session_transaction() as sess:
            sess['_flashes'] = [('message', 'UNIQUE-FLASH-XYZ')]
        resp = client.get('/')
        assert b'UNIQUE-FLASH-XYZ' in resp.data
        fresh = app.test_client()
        resp2 = fresh.get('/')
        assert resp2.status_code == 200
        assert b'UNIQUE-FLASH-XYZ' not in resp2.data


class TestContactForm:
    def test_successful_submission_sends_email(self, client, monkeypatch):
        sent = {}

        def fake_send(name, email, message):
            sent['args'] = (name, email, message)

        monkeypatch.setattr('blueprints.public.send_contact_email', fake_send)
        resp = client.post('/about', data={
            'name': 'Alice', 'email': 'alice@example.com',
            'message': 'I would like to help out with the project.',
        }, follow_redirects=True)
        assert sent['args'][0] == 'Alice'
        assert b'get back to you' in resp.data

    def test_honeypot_blocks_send(self, client, monkeypatch):
        called = {'sent': False}
        monkeypatch.setattr('blueprints.public.send_contact_email',
                            lambda *a: called.__setitem__('sent', True))
        client.post('/about', data={
            'name': 'Bot', 'email': 'bot@example.com',
            'message': 'spam spam spam spam',
            'website': 'http://spam.example',  # honeypot filled
        }, follow_redirects=True)
        assert called['sent'] is False

    def test_send_failure_is_handled_gracefully(self, client, monkeypatch):
        def boom(*a):
            raise RuntimeError('SMTP down')

        monkeypatch.setattr('blueprints.public.send_contact_email', boom)
        resp = client.post('/about', data={
            'name': 'Alice', 'email': 'alice@example.com',
            'message': 'A genuine message about collaborating.',
        }, follow_redirects=True)
        assert b'could not be sent' in resp.data


class TestAccessibilityForm:
    def test_form_404_for_bad_token(self, client):
        assert client.get('/accessibility/nope').status_code == 404

    def test_submitting_creates_accessibility_record(self, client, make_venue):
        venue = make_venue()
        venue.generate_accessibility_token()
        db.session.commit()
        token = venue.accessibility_token

        resp = client.post(f'/accessibility/{token}', data={
            'step_free_entrance': 'yes',
            'accessible_toilet': 'partial',
        }, follow_redirects=True)
        assert resp.status_code == 200
        rec = VenueAccessibility.query.filter_by(venue_id=venue.id).one()
        assert rec.step_free_entrance == 'yes'
        assert rec.accessible_toilet == 'partial'
        assert rec.updated_at is not None

    def test_public_accessibility_page(self, client, make_venue):
        venue = make_venue()
        assert client.get(f'/venues/{venue.id}/accessibility').status_code == 200

    def test_public_accessibility_page_404(self, client):
        assert client.get('/venues/9999/accessibility').status_code == 404


class TestI18n:
    @pytest.mark.parametrize('lang', ['de', 'fr', 'it', 'en'])
    def test_set_language_persists_supported_locale(self, client, lang):
        resp = client.get(f'/set-language/{lang}')
        assert resp.status_code == 302
        with client.session_transaction() as sess:
            assert sess['lang'] == lang

    def test_set_language_rejects_unsupported(self, client):
        assert client.get('/set-language/xx').status_code == 404

    def test_set_language_blocks_protocol_relative_redirect(self, client):
        resp = client.get('/set-language/de?next=//evil.example.com')
        assert 'evil.example.com' not in resp.headers['Location']

    @pytest.mark.parametrize('page', ['impressum', 'agb', 'datenschutz'])
    def test_legal_pages_render_for_valid_lang(self, client, page):
        assert client.get(f'/de/{page}').status_code == 200

    def test_legal_page_404_for_bad_lang(self, client):
        assert client.get('/xx/impressum').status_code == 404


class TestErrorHandlers:
    def test_404_uses_custom_template(self, client):
        resp = client.get('/this-route-does-not-exist')
        assert resp.status_code == 404
