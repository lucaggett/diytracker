"""Human visitor and hit counts for the admin Traffic panel, from nginx logs.

A user agent denylist isn't enough here: most of the site's automated traffic
now presents an ordinary browser UA from a rotating proxy pool, and it can far
outnumber real visitors. So a client has to show evidence of being a person,
not merely fail to look like a known bot.

A client is one (IP, user agent) pair per Swiss calendar day, and counts as a
visitor only when all of these hold:

  * Its UA is a recent desktop or mobile browser, and it named no bot. Browsers
    auto-update, while scrapers tend to freeze an old version into their config.
  * It fetched at least one real public page over HTTP/2 or newer. Scrapers
    often stay on HTTP/1.1, while current browsers negotiate HTTP/2 here.
  * It arrived from somewhere else (a referrer from another site), or it
    browsed: two or more pages, with a same-site referrer on at least one.
    One page view claiming a same-site referrer doesn't count, since a real
    visitor can't arrive that way without an earlier page view. That rule alone
    removes the largest proxy-pool floods seen in the logs.
  * It didn't behave like a crawler or a scanner, and isn't an admin.

This deliberately undercounts: someone who types the address and reads a single
page leaves no evidence and can't be told from a bot. Hits are page views by
those visitors, so stylesheets, feeds and scanner noise never count. The
/static/ and /favicon.ico requests that would show a real browser rendering the
page aren't logged at all (see deploy/nginx-diytracker.conf).

Multi-day visitor totals are sums of daily uniques, as they were under goaccess.
"""

import ipaddress
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from flask import current_app

from diytracker.services.analytics import (
    STATS_WINDOW_DAYS,
    _build_valid_dates,
    _iter_log_lines,
    find_log_files,
    write_stats,
)
from diytracker.services.page_views import canonical_page_path

SWISS_TZ = ZoneInfo("Europe/Zurich")
SITE_HOST = "diytracker.ch"

# Above this, a client is reading the site faster than a person would.
MAX_PAGES_PER_DAY = 60
# Several UAs from one IP in a day is a rotating scraper. Set high enough that
# a household or a small office stays well under it.
MAX_UAS_PER_IP_PER_DAY = 5
MAX_PROBES_PER_DAY = 1
MAX_LANGUAGE_SWITCHES_PER_DAY = 2
# Reading the site in three or more languages in one day is a crawl.
MAX_LOCALES_PER_DAY = 2
# A same-site referrer only shows the client browsed if it viewed this many
# pages that day.
MIN_PAGES_FOR_BROWSING = 2

_LINE_RE = re.compile(
    r"^(?P<ip>\S+) \S+ \S+ \[(?P<stamp>[^\]]+)\] "
    r'"(?P<method>[A-Z]+) (?P<path>\S+) HTTP/(?P<http>[\d.]+)" (?P<status>\d{3}) \S+ '
    r'"(?P<referrer>[^"]*)" "(?P<ua>[^"]*)"'
)
_STAMP_FORMAT = "%d/%b/%Y:%H:%M:%S %z"
_OK_STATUSES = frozenset({"200", "304"})
_BACKSTAGE_STATUSES = frozenset({"200", "302", "304"})

_BROWSER_UA_RE = re.compile(
    r"^Mozilla/5\.0 \((Windows|Macintosh|X11|Linux; Android|iPhone|iPad)[^)]*\)"
    r".*(Chrome|Safari|Firefox|Edg)/"
)
# Wider than event_views._BOT_UA_RE because only UAs that already passed
# _BROWSER_UA_RE get here, so "compatible" can't catch anything but a bot.
_BOT_UA_RE = re.compile(
    r"bot|crawl|spider|slurp|compatible|fetch|scan|python|curl|wget|java|httpx"
    r"|axios|headless|phantom|lighthouse|pagespeed|gtmetrix|preview|externalhit"
    r"|whatsapp|telegram|discord|slack|uptime|monitor|fuzz|ferox|nuclei|zgrab"
    r"|semrush|ahrefs|bytespider|petal|barkrowler|seranking|sitechecker|awario"
    r"|gptbot|claude|openai|perplexity|yandex|baidu|elevenlabs|exa\.ai|archive"
    r"|validator|inspection|gsa-|cfnetwork|networkingextension",
    re.IGNORECASE,
)
_BACKSTAGE_PATH_RE = re.compile(r"^/(admin|queue)(/|$)")
_SET_LANGUAGE_PATH_RE = re.compile(r"^/set-language(/|$)")
_LOCALE_PATH_RE = re.compile(r"^/(en|fr|it)(/|$)")
_PROBE_PATH_RE = re.compile(
    r"\.(env|php|git|asp|aspx|jsp|bak|sql|yml|ini)\b"
    r"|/wp-|/\.|/vendor|/cgi-bin|/config",
    re.IGNORECASE,
)

# Browser release cadence, anchored on versions current in November 2024. Both
# ship about 13 major versions a year; nine versions is roughly eight months,
# which is generous for software that updates itself.
_RELEASE_ANCHOR = (2024, 11)
_CHROME_AT_ANCHOR = 131
_FIREFOX_AT_ANCHOR = 133
_RELEASES_PER_MONTH = 13 / 12
_MAX_VERSIONS_BEHIND = 9
# Safari and iOS ship one major version a year. These are the oldest still
# accepted in 2026; they move up with the calendar.
_OLDEST_SAFARI_2026 = 16
_OLDEST_IOS_2026 = 17

_CHROME_RE = re.compile(r"Chrome/(\d+)")
_FIREFOX_RE = re.compile(r"Firefox/(\d+)")
_SAFARI_RE = re.compile(r"Version/(\d+)")
_IOS_RE = re.compile(r"iPhone OS (\d+)")


def _is_recent_browser(ua, day):
    months = (day.year - _RELEASE_ANCHOR[0]) * 12 + day.month - _RELEASE_ANCHOR[1]
    released = months * _RELEASES_PER_MONTH
    for pattern, anchor in (
        (_CHROME_RE, _CHROME_AT_ANCHOR),
        (_FIREFOX_RE, _FIREFOX_AT_ANCHOR),
    ):
        match = pattern.search(ua)
        if match:
            return int(match[1]) >= anchor + released - _MAX_VERSIONS_BEHIND
    match = _SAFARI_RE.search(ua)
    if match:
        return int(match[1]) >= _OLDEST_SAFARI_2026 + day.year - 2026
    # Other iOS browsers (FxiOS, CriOS) only carry the OS version.
    match = _IOS_RE.search(ua)
    return bool(match) and int(match[1]) >= _OLDEST_IOS_2026 + day.year - 2026


def _referrer_kind(referrer):
    """Returns "internal", "external", or None if it shows nothing."""
    if not referrer or referrer == "-":
        return None
    try:
        host = urlsplit(referrer).hostname
    except ValueError:
        return None
    if not host:
        return None
    try:
        # A link "from" a bare IP is a scanner hitting the server directly.
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return None
    return "internal" if host.removeprefix("www.") == SITE_HOST else "external"


@dataclass
class _Client:
    """One (IP, UA) pair's activity on one Swiss calendar day."""

    pages: int = 0
    http2_pages: int = 0
    internal_referrers: int = 0
    external_referrer: bool = False
    probes: int = 0
    language_switches: int = 0
    locales: set = field(default_factory=set)

    def is_human(self):
        if not self.http2_pages or self.pages > MAX_PAGES_PER_DAY:
            return False
        if (
            self.probes > MAX_PROBES_PER_DAY
            or self.language_switches > MAX_LANGUAGE_SWITCHES_PER_DAY
            or len(self.locales) > MAX_LOCALES_PER_DAY
        ):
            return False
        browsed = self.internal_referrers > 0 and self.pages >= MIN_PAGES_FOR_BROWSING
        return self.external_referrer or browsed


def _record_request(client, match, path, adapter):
    status = match["status"]
    if _SET_LANGUAGE_PATH_RE.match(path):
        client.language_switches += 1
        return
    if status not in _OK_STATUSES:
        if _PROBE_PATH_RE.search(path):
            client.probes += 1
        return
    if match["method"] != "GET" or canonical_page_path(path, adapter) is None:
        return
    client.pages += 1
    if float(match["http"]) >= 2:
        client.http2_pages += 1
    locale = _LOCALE_PATH_RE.match(path)
    client.locales.add(locale[1] if locale else "de")
    kind = _referrer_kind(match["referrer"])
    if kind == "internal":
        client.internal_referrers += 1
    elif kind == "external":
        client.external_referrer = True


def collect_daily_human_counts(lines, first_day, adapter):
    """{date: (page views, visitors)} for the days from *first_day* on.

    Days are Swiss calendar days, not the UTC days the logs are stamped with.
    Returns (counts, requests read) so the caller can tell an empty log window
    from a window that held no humans.
    """
    clients = {}
    uas_per_ip = {}
    admins = set()
    requests_read = 0

    for line in lines:
        match = _LINE_RE.match(line)
        if match is None:
            continue
        try:
            stamp = datetime.strptime(match["stamp"], _STAMP_FORMAT)
        except ValueError:
            continue
        day = stamp.astimezone(SWISS_TZ).date()
        path = match["path"].split("?", 1)[0]
        ip, ua = match["ip"], match["ua"]
        # Checked before the window cut: an admin's earlier sessions identify
        # the client for the whole run.
        if _BACKSTAGE_PATH_RE.match(path) and match["status"] in _BACKSTAGE_STATUSES:
            admins.add((ip, ua))
        if day < first_day:
            continue
        requests_read += 1
        uas_per_ip.setdefault((day, ip), set()).add(ua)
        if not _BROWSER_UA_RE.match(ua) or _BOT_UA_RE.search(ua):
            continue
        client = clients.setdefault((day, ip, ua), _Client())
        _record_request(client, match, path, adapter)

    counts = {}
    for (day, ip, ua), client in clients.items():
        if (
            not client.is_human()
            or (ip, ua) in admins
            or len(uas_per_ip[(day, ip)]) > MAX_UAS_PER_IP_PER_DAY
            or not _is_recent_browser(ua, day)
        ):
            continue
        hits, visitors = counts.get(day, (0, 0))
        counts[day] = (hits + client.pages, visitors + 1)
    return counts, requests_read


def generate_traffic_stats() -> tuple[bool, str]:
    """Recount the Traffic panel's numbers and cache them as JSON. Needs an app
    context for the URL map. A failure leaves the previous cache in place so the
    dashboard keeps showing it.
    """
    log_files = find_log_files()
    if not log_files:
        return False, "No nginx access log files found"

    today = datetime.now(SWISS_TZ).date()
    first_day = today - timedelta(days=2 * STATS_WINDOW_DAYS - 1)
    # Log dates are UTC, so a Swiss day can start on the previous log date.
    valid_dates = _build_valid_dates(first_day - timedelta(days=1), today)
    counts, requests_read = collect_daily_human_counts(
        _iter_log_lines(log_files, valid_dates),
        first_day,
        current_app.url_map.bind("localhost"),
    )
    if not requests_read:
        return False, "No log entries found in stats window"

    write_stats(counts, today)
    return True, f"Stats generated from {len(log_files)} log file(s)"
