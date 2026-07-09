"""Tests for services/scrape_detection.py.

The detector is disabled globally in conftest.py (rapid-fire test traffic
looks like scraping); the `detection` fixture enables it and resets its
in-memory state around each test here.
"""

import json

import pytest

from diytracker.models import ScrapeSuspect
from diytracker.services.scrape_detection import HONEYPOT_PATH, detector

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-CH,en;q=0.9",
    "sec-ch-ua": '"Chromium";v="126", "Google Chrome";v="126"',
    "Sec-Fetch-Site": "same-origin",
}


@pytest.fixture
def detection(app):
    detector.enabled = True
    detector.reset()
    yield detector
    detector.enabled = False
    detector.reset()


def _suspect(ip="127.0.0.1"):
    return ScrapeSuspect.query.filter_by(ip=ip).first()


def test_honeypot_hit_flags_ip(client, detection):
    resp = client.get(HONEYPOT_PATH, headers=BROWSER_HEADERS)
    assert resp.status_code == 200

    suspect = _suspect()
    assert suspect is not None
    assert "honeypot" in json.loads(suspect.signals)
    assert suspect.score >= 5.0


def test_normal_browsing_not_flagged(client, detection):
    for _ in range(20):
        client.get("/", headers=BROWSER_HEADERS)
    assert _suspect() is None


def test_scraper_ua_plus_volume_flags(client, detection):
    headers = {"User-Agent": "python-requests/2.31.0"}
    for _ in range(70):
        client.get("/", headers=headers)

    suspect = _suspect()
    assert suspect is not None
    signals = json.loads(suspect.signals)
    assert "scraper-ua" in signals
    assert "high-rate" in signals


def test_spoofed_browser_headers_detected(app, detection):
    # Browser UA but no Accept-Language and no client hints — a faked UA.
    # Raw test client: the shared `client` fixture injects Accept-Language,
    # which is exactly the header whose absence this test exercises.
    raw_client = app.test_client()
    headers = {"User-Agent": BROWSER_HEADERS["User-Agent"]}
    for _ in range(20):
        raw_client.get("/", headers=headers)

    suspect = _suspect()
    assert suspect is not None
    signals = json.loads(suspect.signals)
    assert "spoofed-browser" in signals
    assert "no-client-hints" in signals


def test_robots_txt_disallows_honeypot(client, detection):
    resp = client.get("/robots.txt")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert f"Disallow: {HONEYPOT_PATH}" in body
    assert "Sitemap:" in body


def test_honeypot_link_present_but_hidden(client, detection):
    resp = client.get("/", headers=BROWSER_HEADERS)
    body = resp.get_data(as_text=True)
    assert HONEYPOT_PATH in body
    assert 'rel="nofollow"' in body


def test_logged_in_users_ignored(client, detection, admin):
    client.post("/login", data={"email": "admin@example.com", "password": "adminpass"})
    client.get(HONEYPOT_PATH, headers=BROWSER_HEADERS)
    assert _suspect() is None


def test_admin_suspects_page(client, detection, admin):
    client.get(HONEYPOT_PATH, headers=BROWSER_HEADERS)
    assert _suspect() is not None

    client.post("/login", data={"email": "admin@example.com", "password": "adminpass"})
    resp = client.get("/admin/scrape-suspects")
    assert resp.status_code == 200
    assert b"honeypot" in resp.data

    resp = client.post("/admin/scrape-suspects/clear", follow_redirects=True)
    assert resp.status_code == 200
    assert _suspect() is None
