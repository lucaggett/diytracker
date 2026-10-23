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

from urllib.parse import urlsplit

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


def is_same_origin_navigation():
    """True when the request came from a link on one of the site's own pages.

    Sec-Fetch-Site is set by the browser itself and can't be changed from page
    script; every current browser sends it. The Referer check covers older
    ones, and it holds under the site's strict-origin-when-cross-origin policy,
    which sends the full URL on same-origin navigations.
    """
    if request.headers.get("Sec-Fetch-Site") == "same-origin":
        return True
    referer = request.headers.get("Referer", "")
    return bool(referer) and urlsplit(referer).netloc == request.host


def cross_origin_switch_response():
    """Refusal for a /set-language/ request that didn't come from the site.

    From July 2026 a scraper on a rotating residential proxy pool fetched
    pages through harvested switcher URLs: one IP per page, two requests
    each, no Referer, a randomised macOS Chrome UA. That pattern gets past
    the per-IP rate limit, the UA list and scrape_detection's per-IP scoring,
    and it swamped the admin TUI's device stats with "macOS humans". A real
    switch is always a click on a page here, so refusing everything else
    costs visitors nothing and stops the scraper getting the page.
    """
    current_app.logger.info(
        "refused set-language without same-origin navigation: ip=%s ua=%r",
        request.remote_addr,
        (request.headers.get("User-Agent") or "")[:200],
    )
    return Response(
        "Language switching only works from a link on this site.\n",
        status=403,
        mimetype="text/plain",
        headers={"X-Robots-Tag": "noindex, nofollow"},
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
