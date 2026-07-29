"""The abuse guard and the crawl-trap fixes around /set-language/.

Background: a client identifying as apache-httpd walked production hard in
July 2026, concentrating on /set-language/ because the switcher put one
crawlable link per locale on every page. These tests pin both halves of the
fix — stop advertising the route, and reject the client outright.
"""

from diytracker.services.limits import limiter

BLOCKED_UA = "apache-httpd/4.5.13 (Java/1.8.0_292)"
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


# --------------------------------------------------------------------------
# Blocked clients
# --------------------------------------------------------------------------


def test_blocked_user_agent_gets_429(client):
    resp = client.get("/", headers={"User-Agent": BLOCKED_UA})
    assert resp.status_code == 429
    assert resp.headers["Retry-After"]
    assert resp.mimetype == "text/plain"


def test_blocked_user_agent_match_is_case_insensitive(client):
    assert client.get("/", headers={"User-Agent": "Apache-HttpD/5"}).status_code == 429


def test_browser_user_agent_is_unaffected(client):
    assert client.get("/", headers={"User-Agent": BROWSER_UA}).status_code == 200


def test_missing_user_agent_is_unaffected(client):
    # Absent UA is the detector's business (it scores it), not the guard's.
    assert client.get("/", headers={"User-Agent": ""}).status_code == 200


def test_api_blueprint_is_exempt(client):
    # /api/ingest is token-authenticated and meant to be called by scripts, so
    # an HTTP-library user agent there is expected rather than abusive.
    resp = client.post("/api/ingest", headers={"User-Agent": BLOCKED_UA}, json={})
    assert resp.status_code != 429


def test_blocked_client_never_reaches_the_page(client):
    body = client.get("/", headers={"User-Agent": BLOCKED_UA}).get_data(as_text=True)
    assert "<html" not in body.lower()


# --------------------------------------------------------------------------
# /set-language/ is no longer a crawl trap
# --------------------------------------------------------------------------


def test_robots_disallows_set_language(client):
    body = client.get("/robots.txt").get_data(as_text=True)
    assert "Disallow: /set-language/" in body


def test_language_switcher_links_are_nofollow(client):
    html = client.get("/").get_data(as_text=True)
    assert 'href="/set-language/' in html
    for chunk in html.split('href="/set-language/')[1:]:
        # rel="nofollow" sits on the same tag, before the closing '>'.
        assert 'rel="nofollow"' in chunk.split(">")[0]


def test_set_language_response_is_noindex_nofollow(client):
    resp = client.get("/set-language/fr?next=/about")
    assert resp.status_code == 302
    assert resp.headers["X-Robots-Tag"] == "noindex, nofollow"


def test_set_language_is_rate_limited(client):
    limiter.enabled = True
    try:
        codes = [client.get("/set-language/fr").status_code for _ in range(40)]
    finally:
        limiter.enabled = False
    assert 429 in codes, f"/set-language/ never rate-limited: {set(codes)}"
