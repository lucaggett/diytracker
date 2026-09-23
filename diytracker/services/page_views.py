"""Most visited public pages over the analytics window, language variants
merged, distilled from nginx access logs.

Runs on the same 15-minute schedule as services/event_views.py and filters
the same way (successful GETs, UA bot denylist), but keeps no history: each
run recounts the last STATS_WINDOW_DAYS days and overwrites a small JSON
cache, like the Traffic panel's analytics_stats.json.

Paths are resolved against the app's own URL map, so only real public pages
count — assets, feeds, backstage routes, the honeypot and scanner noise that
happens to return 200 all fall out — and every language variant of a page
(/fr/events/12/, /en/events/12/, /events/12/; /fr/impressum, /de/impressum)
collapses onto its default-locale URL. Visitors are distinct IPs across the
whole window and all variants, so someone reading a page in two languages
counts once.
"""

import json
import re
from datetime import datetime, timedelta
from urllib.parse import unquote

from flask import current_app
from werkzeug.exceptions import HTTPException

from diytracker.paths import ROOT
from diytracker.services.analytics import (
    STATS_WINDOW_DAYS,
    _build_valid_dates,
    _iter_filtered_lines,
    find_log_files,
)
from diytracker.services.event_views import _BOT_UA_RE
from diytracker.services.i18n import DEFAULT_LOCALE

PAGE_VIEWS_PATH = ROOT / "instance" / "page_views.json"
TOP_N = 25

# Pages a visitor reads. Everything else on the public blueprints is either
# machine-facing (.ics, sitemap, robots, llms.txt), a redirect
# (set-language), the scrape honeypot, or the private accessibility-token form
# whose URL is a credential and must not be listed anywhere.
_PAGE_ENDPOINTS = frozenset(
    {
        "public.calendar_view",
        "public.about",
        "public.help_page",
        "public.legal",
        "public.venue_accessibility",
        "public.event_page",
        "public.venue_map",
        "public.venue_page",
        "public.canton_page",
        "public.genre_page",
        "public.label_page",
        "public.search",
        "public.archive_index",
        "public.archive_month",
        "public.kyuubi",
        "submissions.submit_event_link",
    }
)

# Combined log format, any path; status and UA checked below.
_LINE_RE = re.compile(
    r"^(?P<ip>\S+) \S+ \S+ \[[^\]]*\] "
    r'"GET (?P<path>\S+) HTTP[^"]*" (?P<status>\d{3}) \S+ '
    r'"[^"]*" "(?P<ua>[^"]*)"'
)


def canonical_page_path(path, adapter):
    """The default-locale path of a public page, or None if *path* isn't one.

    *adapter* is a bound werkzeug MapAdapter (url_map.bind(...)).
    """
    path = unquote(path.split("?", 1)[0])
    try:
        endpoint, args = adapter.match(path, method="GET")
    except HTTPException:  # 404, 405, and slash-appending RequestRedirects
        return None
    if endpoint not in _PAGE_ENDPOINTS:
        return None
    prefix = args.get("lang_prefix")
    if prefix:
        return path[len(prefix) + 1 :] or "/"
    if endpoint == "public.legal":
        return f"/{DEFAULT_LOCALE}/{args['doc']}"
    return path


def collect_page_counts(files, valid_dates, adapter):
    """{canonical path: (hits, distinct_ip_count)} over the given logs."""
    counts = {}
    for line in _iter_filtered_lines(files, valid_dates):
        if '"GET ' not in line:
            continue
        match = _LINE_RE.match(line)
        if match is None or match["status"] != "200":
            continue
        ua = match["ua"].strip()
        if not ua or ua == "-" or _BOT_UA_RE.search(ua):
            continue
        page = canonical_page_path(match["path"], adapter)
        if page is None:
            continue
        entry = counts.setdefault(page, [0, set()])
        entry[0] += 1
        entry[1].add(match["ip"])
    return {page: (hits, len(ips)) for page, (hits, ips) in counts.items()}


def generate_page_view_stats() -> tuple[bool, str]:
    """Recount the top pages for the analytics window and cache them as JSON.
    Mirrors analytics.generate_stats()'s (success, message) contract; needs an
    app context for the URL map.
    """
    log_files = find_log_files()
    if not log_files:
        return False, "No nginx access log files found"

    today = datetime.now().date()
    valid_dates = _build_valid_dates(
        today - timedelta(days=STATS_WINDOW_DAYS - 1), today
    )
    adapter = current_app.url_map.bind("localhost")
    counts = collect_page_counts(log_files, valid_dates, adapter)
    ranked = sorted(counts.items(), key=lambda item: (-item[1][0], item[0]))
    stats = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "window_days": STATS_WINDOW_DAYS,
        "pages": [
            {"path": page, "hits": hits, "visitors": visitors}
            for page, (hits, visitors) in ranked[:TOP_N]
        ],
    }
    PAGE_VIEWS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = PAGE_VIEWS_PATH.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(stats))
    tmp_path.replace(PAGE_VIEWS_PATH)
    return True, f"Page view counts updated for {len(counts)} page(s)"


def read_page_views() -> dict | None:
    try:
        return json.loads(PAGE_VIEWS_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return None
