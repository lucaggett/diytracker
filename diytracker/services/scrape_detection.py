"""Inbound scraping detection.

Scores every anonymous request against a set of signals and persists a
ScrapeSuspect row once an IP crosses the flag threshold. Detection only —
nothing is blocked; suspects show up under /admin/scrape-suspects.

Signals (weights in _score_ip):
  * honeypot        — fetched HONEYPOT_PATH, which is linked invisibly in the
                      footer and disallowed in robots.txt. Humans never see
                      it, polite crawlers skip it.
  * scraper-ua      — User-Agent of a known HTTP library or headless browser.
  * missing-ua      — no User-Agent at all.
  * spoofed-browser — claims to be a browser but lacks Accept-Language, which
                      every real browser sends (evasion: faked User-Agent).
  * no-client-hints — claims modern Chrome but sends no sec-ch-ua client
                      hints (evasion: faked Chrome UA outside a real Chrome).
  * high-rate       — sustained page volume no human produces.
  * metronomic      — inter-request intervals too uniform, even with the
                      randomised sleep jitter scrapers add to look human.
  * enumeration     — sequential sweep of /events/<id> pages.
  * no-navigation   — many pages but never a same-origin Referer or
                      Sec-Fetch-Site: same-origin (nobody clicks links).
  * shared-client   — same header fingerprint active from many IPs at once
                      (evasion: rotating proxies). Only counted for IPs that
                      already show another signal, since real browsers share
                      fingerprints too.

State is in-memory and per-process: with 4 gunicorn workers each sees ~1/4
of an IP's traffic, so volume thresholds are effectively up to 4x — same
trade-off flask-limiter already makes in services/limits.py.
"""

import json
import re
import statistics
import threading
import time
from collections import OrderedDict, deque

from flask import current_app, request, session

from diytracker.models import ScrapeSuspect, db, utcnow

# Linked (hidden) from the public footer and disallowed in robots.txt.
HONEYPOT_PATH = "/events/archive/"

WINDOW_SECONDS = 600
MAX_TRACKED_IPS = 4096
FLAG_THRESHOLD = 4.0
PERSIST_INTERVAL_SECONDS = 60

_SCRAPER_UA_RE = re.compile(
    r"python-requests|python-urllib|scrapy|httpx|aiohttp|curl|wget"
    r"|go-http-client|libwww|java/|okhttp|node-fetch|axios|undici"
    r"|phantomjs|headless|selenium|playwright|puppeteer",
    re.IGNORECASE,
)
_BROWSER_UA_RE = re.compile(r"Mozilla/", re.IGNORECASE)
_SEARCH_ENGINE_UA_RE = re.compile(
    r"Googlebot|bingbot|DuckDuckBot|Applebot|YandexBot|Baiduspider", re.IGNORECASE
)
_CHROME_UA_RE = re.compile(r"Chrome/(\d+)")
_EVENT_PAGE_RE = re.compile(r"^/events/(\d+)/$")


class _IpState:
    __slots__ = (
        "times",
        "event_ids",
        "internal_nav",
        "page_count",
        "signals",
        "sample_paths",
        "last_persist",
        "persisted_pages",
        "persisted_signals",
        "user_agent",
    )

    def __init__(self):
        self.times = deque(maxlen=200)
        self.event_ids = deque(maxlen=30)
        self.internal_nav = False
        self.page_count = 0
        self.signals = {}  # name -> weight, sticky within the window
        self.sample_paths = deque(maxlen=8)
        self.last_persist = 0.0
        self.persisted_pages = 0
        self.persisted_signals = set()
        self.user_agent = ""


class ScrapeDetector:
    def __init__(self):
        self.enabled = True
        self._lock = threading.Lock()
        self._ips: OrderedDict[str, _IpState] = OrderedDict()
        # header-fingerprint -> {ip: last_seen} for rotating-proxy detection
        self._fingerprints: dict[str, dict[str, float]] = {}

    def init_app(self, app):
        app.before_request(self._on_request)

    def reset(self):
        with self._lock:
            self._ips.clear()
            self._fingerprints.clear()

    # ------------------------------------------------------------------

    def _on_request(self, *_args):
        if not self.enabled:
            return
        # Static files are served by nginx in production, so seeing them here
        # is not meaningful either way; logged-in users are trusted.
        if request.endpoint == "static" or "user_id" in session:
            return
        # Sitemap and robots.txt exist for crawlers; hitting them must never
        # contribute to a scraper score.
        if request.endpoint in ("public.sitemap", "public.robots"):
            return
        # Audit marker only (UA is self-declared, so no scoring exemption):
        # makes search-engine crawl activity greppable in the logs.
        ua = request.headers.get("User-Agent", "")
        if _SEARCH_ENGINE_UA_RE.search(ua):
            current_app.logger.info(
                "search-engine crawler: ua=%r path=%s ip=%s",
                ua,
                request.path,
                request.remote_addr,
            )

        ip = request.remote_addr or "unknown"
        now = time.monotonic()

        with self._lock:
            state = self._ips.get(ip)
            if state is None:
                if len(self._ips) >= MAX_TRACKED_IPS:
                    self._ips.popitem(last=False)
                state = self._ips[ip] = _IpState()
            self._ips.move_to_end(ip)
            self._observe(state, ip, now)
            score = sum(state.signals.values())
            # Persist on a fresh signal type immediately, otherwise at most
            # once a minute so a flagged IP doesn't turn into a write per hit.
            should_persist = score >= FLAG_THRESHOLD and (
                not state.signals.keys() <= state.persisted_signals
                or now - state.last_persist >= PERSIST_INTERVAL_SECONDS
            )
            if should_persist:
                state.last_persist = now
                state.persisted_signals |= state.signals.keys()
                new_pages = state.page_count - state.persisted_pages
                state.persisted_pages = state.page_count
                snapshot = (
                    score,
                    dict(state.signals),
                    new_pages,
                    list(state.sample_paths),
                    state.user_agent,
                )

        if should_persist:
            self._persist(ip, *snapshot)

    def _observe(self, state, ip, now):
        # Drop observations older than the window so signals can decay.
        while state.times and now - state.times[0] > WINDOW_SECONDS:
            state.times.popleft()
        if not state.times:
            state.signals.clear()
            state.persisted_signals.clear()
            state.page_count = 0
            state.persisted_pages = 0
            state.internal_nav = False
            state.event_ids.clear()
        state.times.append(now)
        state.page_count += 1
        state.user_agent = (request.user_agent.string or "")[:300]
        if request.path not in state.sample_paths:
            state.sample_paths.append(request.path[:200])

        ua = state.user_agent
        headers = request.headers

        if request.path == HONEYPOT_PATH:
            state.signals["honeypot"] = 5.0

        if not ua:
            state.signals["missing-ua"] = 2.0
        elif _SCRAPER_UA_RE.search(ua):
            state.signals["scraper-ua"] = 3.0
        elif _BROWSER_UA_RE.search(ua):
            if not headers.get("Accept-Language"):
                state.signals["spoofed-browser"] = 2.5
            chrome = _CHROME_UA_RE.search(ua)
            if chrome and int(chrome.group(1)) >= 90 and not headers.get("sec-ch-ua"):
                state.signals["no-client-hints"] = 1.5

        referer = headers.get("Referer", "")
        if request.host in referer or headers.get("Sec-Fetch-Site") == "same-origin":
            state.internal_nav = True
        if state.page_count >= 15 and not state.internal_nav:
            state.signals["no-navigation"] = 1.5

        n = len(state.times)
        if n > 150:
            state.signals["high-rate"] = 3.5
        elif n > 60:
            state.signals["high-rate"] = 2.0

        if n >= 10:
            intervals = [
                b - a for a, b in zip(list(state.times)[-11:], list(state.times)[-10:])
            ]
            mean = statistics.fmean(intervals)
            if 0 < mean < 15 and statistics.pstdev(intervals) / mean < 0.25:
                state.signals["metronomic"] = 2.0

        m = _EVENT_PAGE_RE.match(request.path)
        if m:
            state.event_ids.append(int(m.group(1)))
            ids = list(state.event_ids)[-8:]
            if len(ids) >= 8 and all(a < b for a, b in zip(ids, ids[1:])):
                state.signals["enumeration"] = 2.0

        self._check_shared_client(state, ip, now, ua, headers)

    def _check_shared_client(self, state, ip, now, ua, headers):
        fp = "|".join(
            (ua, headers.get("Accept-Language", ""), headers.get("Accept", ""))
        )
        seen = self._fingerprints.setdefault(fp, {})
        seen[ip] = now
        if len(seen) > 8:
            for other, ts in list(seen.items()):
                if now - ts > WINDOW_SECONDS:
                    del seen[other]
        if len(self._fingerprints) > MAX_TRACKED_IPS:
            self._fingerprints.pop(next(iter(self._fingerprints)))
        # Shared fingerprints are only suspicious alongside another signal:
        # identical stock-browser headers are common across real users.
        if len(seen) >= 8 and state.signals:
            state.signals["shared-client"] = 2.0

    # ------------------------------------------------------------------

    def _persist(self, ip, score, signals, page_count, sample_paths, user_agent):
        try:
            suspect = ScrapeSuspect.query.filter_by(ip=ip).first()
            if suspect is None:
                suspect = ScrapeSuspect(ip=ip, first_seen=utcnow())
                db.session.add(suspect)
            suspect.last_seen = utcnow()
            suspect.score = max(suspect.score or 0.0, score)
            suspect.request_count = (suspect.request_count or 0) + page_count
            merged = set(json.loads(suspect.signals or "[]")) | set(signals)
            suspect.signals = json.dumps(sorted(merged))
            suspect.user_agent = user_agent
            suspect.sample_paths = json.dumps(sample_paths)
            db.session.commit()
        except Exception:
            # Detection must never take a public request down with it.
            db.session.rollback()


detector = ScrapeDetector()
