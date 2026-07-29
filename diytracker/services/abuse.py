"""Cheap rejection of clients that have no business browsing the site.

Deliberately separate from services/scrape_detection.py, which scores traffic
and never blocks. This is the opposite: no scoring, no state, no database — a
substring match on the User-Agent (or the client IP) and an immediate 429.

The user agents listed here are HTTP libraries and load generators, not
browsers with a slightly odd string: nobody loses a page they wanted. Anything
subtler belongs in the detector, where a human reviews it under
/admin/scrape-suspects before acting.

The response is plain text on purpose — rendering an error template would cost
a template render and pull in the context processors (which query the DB for
footer cantons and genres), and it would need translating into four languages
for a reader that is not a person.
"""

from flask import Response, current_app, request

# Matched case-insensitively as substrings of the User-Agent.
#
# "apache-httpd" (the Apache HttpClient family) turned up in July 2026 walking
# the site hard, concentrating on /set-language/ — the switcher links used to
# be crawlable, so following links multiplied instead of converging. That is
# fixed in robots()/lang_switcher.html; this list stops the client that
# ignores both.
DEFAULT_BLOCKED_USER_AGENTS = (
    "apache-httpd",
    "apache-httpclient",
)

RETRY_AFTER_SECONDS = 3600


def _blocked_response():
    return Response(
        "Automated traffic from this client is not accepted.\n",
        status=429,
        mimetype="text/plain",
        headers={"Retry-After": str(RETRY_AFTER_SECONDS)},
    )


def init_abuse_guard(app):
    blocked_uas = tuple(
        ua.lower()
        for ua in (
            *DEFAULT_BLOCKED_USER_AGENTS,
            *app.config.get("BLOCKED_USER_AGENTS", ()),
        )
        if ua
    )
    blocked_ips = frozenset(ip for ip in app.config.get("BLOCKED_IPS", ()) if ip)

    if not blocked_uas and not blocked_ips:
        return

    @app.before_request
    def _reject_blocked_clients():
        # /api/ingest authenticates with a bearer token and is *meant* to be
        # called by scripts, which legitimately send HTTP-library user agents.
        if request.blueprint == "api":
            return None

        ip = request.remote_addr or ""
        if ip and ip in blocked_ips:
            current_app.logger.info("blocked client: ip=%s path=%s", ip, request.path)
            return _blocked_response()

        ua = (request.headers.get("User-Agent") or "").lower()
        if ua and any(blocked in ua for blocked in blocked_uas):
            current_app.logger.info(
                "blocked client: ua=%r ip=%s path=%s", ua[:200], ip, request.path
            )
            return _blocked_response()

        return None
