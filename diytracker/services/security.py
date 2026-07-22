"""Security response headers.

Set on the app rather than in nginx so they ship with a normal git deploy
and cover every app response identically. nginx still serves /static/
directly, so those responses don't get them — that's fine, they are our own
CSS/JS/images with no script execution context of their own.

The CSP is nonce-based on purpose. `script-src 'unsafe-inline'` would allow
`javascript:` hrefs, which is exactly the class of bug the policy is here to
contain; a nonce blocks them. Every inline <script> in templates/ therefore
carries nonce="{{ csp_nonce() }}". Inline *styles* keep 'unsafe-inline':
templates use style="" attributes in a dozen places and injected CSS is not
a comparable risk.

CSP_ENFORCE is False for now, so the policy ships as Report-Only and cannot
break the site. Two things must be fixed before flipping it, or the calendar
loses its JS:

1. Inline `onclick=`/`onsubmit=` handlers (calendar.html, event_cards.html,
   event_queue.html, venues.html, admin.html, genre_manage.html,
   promoter_dashboard.html) are blocked by a nonce policy. They need to
   become addEventListener bindings in a static .js file.
2. The public pages are cached by @cache.cached, which stores the rendered
   body *before* after_request adds this header — so a cached page carries a
   stale nonce while the header carries a fresh one, and every inline script
   on it is rejected. The fix is to have no inline scripts on cached pages
   at all (move calendar.html's block to static/js/), not to try to keep the
   two in sync.

Until then Report-Only gives real violation data from real browsers with no
risk. tests/test_security.py asserts the mode, so flipping it is deliberate.
"""

import secrets

from flask import g

# Third-party origins the pages legitimately load from.
_CDN = "https://cdn.jsdelivr.net"  # choices.js + flatpickr on the event forms
_TILES = "https://tile.openstreetmap.org"  # leaflet map tiles

# Responses that are not our own templates and must not get the CSP: the
# goaccess analytics report is third-party HTML with its own inline scripts
# and styles, and it would simply render blank under the policy.
_CSP_EXEMPT_ENDPOINTS = {"admin.analytics_view"}

# Flip to True only after the two blockers in the module docstring are done.
CSP_ENFORCE = False


def _csp(nonce):
    return "; ".join(
        [
            "default-src 'self'",
            f"script-src 'self' 'nonce-{nonce}' {_CDN}",
            f"style-src 'self' 'unsafe-inline' {_CDN}",
            f"img-src 'self' data: {_TILES}",
            "font-src 'self' data:",
            "connect-src 'self'",
            "object-src 'none'",
            "base-uri 'self'",
            "form-action 'self'",
            "frame-ancestors 'none'",
        ]
    )


def init_security_headers(app):
    @app.before_request
    def _make_nonce():
        g.csp_nonce = secrets.token_urlsafe(16)

    @app.context_processor
    def _expose_nonce():
        return {"csp_nonce": lambda: getattr(g, "csp_nonce", "")}

    @app.after_request
    def _set_security_headers(response):
        from flask import request

        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault(
            "Referrer-Policy", "strict-origin-when-cross-origin"
        )
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault(
            "Permissions-Policy", "geolocation=(), camera=(), microphone=(), payment=()"
        )
        # Only meaningful over TLS, and asserting it on a plain-http dev
        # server would pin localhost to https in the browser for a year.
        if request.is_secure:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        if request.endpoint not in _CSP_EXEMPT_ENDPOINTS:
            header = (
                "Content-Security-Policy"
                if CSP_ENFORCE
                else "Content-Security-Policy-Report-Only"
            )
            response.headers.setdefault(header, _csp(getattr(g, "csp_nonce", "")))
        return response
