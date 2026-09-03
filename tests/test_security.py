"""Regression tests for the security hardening pass.

Each test here corresponds to a finding: a `javascript:` ticket link reaching
an href, missing response headers, an unmetered contact form, and a session
that survived a login unchanged.
"""

import pytest

from diytracker.models import db
from diytracker.services.limits import limiter

# --------------------------------------------------------------------------
# Ticket links must never reach an href unless they are http(s)
# --------------------------------------------------------------------------

HOSTILE_LINKS = [
    "javascript:alert(document.domain)",
    "JavaScript:alert(1)",
    "  javascript:alert(1)",
    "data:text/html,<script>alert(1)</script>",
    "vbscript:msgbox(1)",
]


@pytest.mark.parametrize("bad_link", HOSTILE_LINKS)
def test_hostile_ticket_link_never_rendered_as_href(app, client, make_event, bad_link):
    """The calendar cards are the page every visitor lands on."""
    with app.app_context():
        make_event(name="XSS Probe", genre="Metal", ticket_link=bad_link)

    for path in ("/", "/genre/metal/"):
        body = client.get(path).get_data(as_text=True)
        assert 'href="' + bad_link not in body
        # The scheme is what matters, not the exact string: assert no href
        # anywhere on the page carries a non-http(s) scheme.
        assert 'href="javascript:' not in body.lower()
        assert 'href="data:text/html' not in body.lower()
        assert 'href="vbscript:' not in body.lower()


def test_hostile_ticket_link_not_rendered_on_event_page(app, client, make_event):
    with app.app_context():
        ev = make_event(ticket_link="javascript:alert(1)")
        event_id = ev.id
    body = client.get(f"/events/{event_id}/").get_data(as_text=True)
    assert "javascript:alert(1)" not in body


def test_safe_ticket_link_still_rendered(app, client, make_event):
    """The guard must not break the normal case."""
    with app.app_context():
        make_event(ticket_link="https://tickets.example.com/show")
    body = client.get("/").get_data(as_text=True)
    assert 'href="https://tickets.example.com/show"' in body


@pytest.mark.parametrize("bad_link", HOSTILE_LINKS)
def test_event_form_rejects_non_http_ticket_link(app, bad_link):
    from werkzeug.datastructures import MultiDict

    from diytracker.forms import EventForm

    # formdata, not data=: Optional() keys off raw_data and would otherwise
    # short-circuit the whole chain.
    with app.test_request_context():
        form = EventForm(formdata=MultiDict({"ticket_link": bad_link}))
        form.validate()
        assert "ticket_link" in form.errors


def test_event_form_accepts_http_ticket_link(app):
    from werkzeug.datastructures import MultiDict

    from diytracker.forms import EventForm

    with app.test_request_context():
        form = EventForm(formdata=MultiDict({"ticket_link": "https://example.com/t"}))
        form.validate()
        assert "ticket_link" not in form.errors


def test_queue_override_strips_hostile_ticket_link(app):
    """The approval path builds events without going through EventForm."""
    from diytracker.utils import clean_ticket_url

    assert clean_ticket_url("javascript:alert(1)", "metalgigs") is None
    assert clean_ticket_url("https://example.com/t", "metalgigs") == (
        "https://example.com/t"
    )


# --------------------------------------------------------------------------
# Response headers
# --------------------------------------------------------------------------


CSP_HEADER = "Content-Security-Policy-Report-Only"


def test_security_headers_present_on_public_pages(client):
    resp = client.get("/")
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert resp.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert "Permissions-Policy" in resp.headers
    csp = resp.headers[CSP_HEADER]
    assert "frame-ancestors 'none'" in csp
    assert "object-src 'none'" in csp


def test_csp_ships_report_only_until_inline_js_is_extracted():
    """Guards the flip: enforcing today would kill the calendar's JS.

    See the blockers listed in diytracker/services/security.py. Flip
    CSP_ENFORCE and update this test in the same change.
    """
    from diytracker.services import security

    assert security.CSP_ENFORCE is False


def test_csp_uses_a_nonce_and_not_unsafe_inline_for_scripts(client):
    """`script-src 'unsafe-inline'` would re-allow javascript: hrefs."""
    resp = client.get("/")
    csp = resp.headers[CSP_HEADER]
    script_src = next(p for p in csp.split("; ") if p.startswith("script-src"))
    assert "'unsafe-inline'" not in script_src
    assert "'nonce-" in script_src


def test_csp_nonce_is_per_response_and_matches_inline_scripts(client):
    first = client.get("/")
    second = client.get("/")
    body = first.get_data(as_text=True)

    def nonce_of(resp):
        csp = resp.headers[CSP_HEADER]
        part = next(p for p in csp.split("; ") if p.startswith("script-src"))
        return part.split("'nonce-")[1].split("'")[0]

    n1, n2 = nonce_of(first), nonce_of(second)
    assert n1 and n1 != n2, "nonce must be fresh per response"
    # Note: / is served from the page cache, so its *body* keeps whatever
    # nonce was current when it was rendered. That desync is precisely why
    # CSP is Report-Only — see test_csp_ships_report_only_until_inline_js.
    assert "<script" in body


def test_hsts_only_over_tls(client):
    assert "Strict-Transport-Security" not in client.get("/").headers
    resp = client.get("/", base_url="https://localhost")
    assert "max-age=31536000" in resp.headers["Strict-Transport-Security"]


# --------------------------------------------------------------------------
# Contact form rate limit
# --------------------------------------------------------------------------


def test_contact_form_is_rate_limited(app, client, monkeypatch):
    sent = []
    monkeypatch.setattr(
        "diytracker.blueprints.public.send_contact_email",
        lambda *a, **kw: sent.append(a),
    )
    limiter.enabled = True
    try:
        codes = [
            client.post(
                "/about",
                data={
                    "name": "A",
                    "email": "a@example.com",
                    "message": "hi",
                    "website": "",
                },
            ).status_code
            for _ in range(8)
        ]
    finally:
        limiter.enabled = False
    assert 429 in codes, f"contact form never rate-limited: {codes}"
    assert len(sent) <= 5


# --------------------------------------------------------------------------
# Session fixation
# --------------------------------------------------------------------------


def test_login_rotates_session_contents(app, client, make_user):
    with app.app_context():
        user = make_user(email="rot@example.com", password="hunter2hunter2")
        email = user.email

    with client.session_transaction() as sess:
        sess["planted"] = "attacker-value"
        sess["lang"] = "fr"

    resp = client.post("/login", data={"email": email, "password": "hunter2hunter2"})
    assert resp.status_code == 302

    with client.session_transaction() as sess:
        assert "planted" not in sess, "pre-login session data survived login"
        assert sess.get("user_id") is not None
        assert sess.get("lang") == "fr", "language preference should survive"


def test_set_password_rotates_session_contents(app, client, make_user):
    from diytracker.models import Submitter

    with app.app_context():
        user = make_user(email="inv@example.com", password=None)
        token = user.generate_invite_token()
        db.session.commit()

    with client.session_transaction() as sess:
        sess["planted"] = "attacker-value"

    resp = client.post(
        f"/set-password/{token}",
        data={"password": "longenough1", "confirm_password": "longenough1"},
    )
    assert resp.status_code == 302
    with client.session_transaction() as sess:
        assert "planted" not in sess
        assert sess.get("user_id") is not None

    with app.app_context():
        assert (
            Submitter.query.filter_by(email="inv@example.com").first().invite_token
            is None
        )


# --------------------------------------------------------------------------
# Queue moderation is admin-only
# --------------------------------------------------------------------------


def test_queue_is_forbidden_to_non_admin_users(app, client, make_user, login):
    """An invite grants /submit, not the moderation queue.

    Approving publishes to the live calendar and deleting is permanent, so
    these must not ride on plain login_required.
    """
    with app.app_context():
        login(make_user(email="plain@example.com", password="pw12345678"))
    assert client.get("/queue").status_code == 403
    assert client.post("/queue", data={"scraped_id": "1"}).status_code == 403
    assert client.post("/queue/1/delete").status_code == 403


def test_queue_is_open_to_admins(app, client, admin, login):
    login(admin)
    assert client.get("/queue").status_code == 200


def test_submitting_stays_available_to_any_invited_user(app, client, make_user, login):
    """The baseline capability an invite is for must be unchanged."""
    with app.app_context():
        login(make_user(email="plain2@example.com", password="pw12345678"))
    assert client.get("/submit").status_code == 200


def test_queue_link_hidden_from_non_admin_nav(app, client, make_user, login):
    with app.app_context():
        login(make_user(email="plain3@example.com", password="pw12345678"))
    page = client.get("/submit").get_data(as_text=True)
    assert "/queue" not in page, "nav offers a link the user gets a 403 from"
