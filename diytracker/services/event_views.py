"""Per-event page view counts distilled from nginx access logs.

Runs right after the goaccess stats pass (services/analytics.py) on the same
15-minute schedule, but parses the logs directly in Python: the goaccess JSON
requests panel only reports per-URL totals over the whole parsed window, and
those totals shrink as old days rotate out of the logs, so they can't be
accumulated safely. Instead every run recomputes per-(event, day) counts for
all days still inside the log window and upserts them with MAX — reruns are
idempotent, today's partial counts only grow, and rows for days that have
rotated out of the logs are simply never touched again. That untouched tail
is the durable history.

Bot filtering approximates goaccess --ignore-crawlers with a UA denylist, so
per-event numbers will differ slightly from the Traffic panel's totals; both
are trend indicators, not a census. Reading the logs a second time per run
(separately from the goaccess pass) is deliberate: sharing one pass would
entangle analytics.py, and the filtered window is small.
"""

import re
from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import joinedload

from diytracker.models import Event, EventDailyViews, db
from diytracker.services.analytics import (
    STATS_WINDOW_DAYS,
    _build_valid_dates,
    _iter_filtered_lines,
    find_log_files,
)

# Combined log format: IP ident user [date] "request" status size "ref" "ua".
# Only successful GETs of event detail pages count; the path regex mirrors
# _EVENT_PAGE_RE in services/scrape_detection.py (trailing slash required, so
# the /events/archive/ honeypot never matches).
_LINE_RE = re.compile(
    r"^(?P<ip>\S+) \S+ \S+ \[(?P<day>[^:\]]+)[^\]]*\] "
    r'"GET /events/(?P<event_id>\d+)/ HTTP[^"]*" (?P<status>\d{3}) \S+ '
    r'"[^"]*" "(?P<ua>[^"]*)"'
)

# Same spirit as deploy/traffic_audit.sh's botrx: crawlers, HTTP libraries and
# headless clients. Blank/"-" UAs are rejected separately.
_BOT_UA_RE = re.compile(
    r"bot|crawl|spider|slurp|scan|monitor|preview|awario|semrush|ahrefs"
    r"|bytespider|petal|amazonbot|headless|python|curl|wget|scrapy|go-http"
    r"|okhttp|aiohttp|httpx|httpclient|libwww|java/|zgrab|facebookexternalhit",
    re.IGNORECASE,
)


def _parse_line(line):
    """(event_id, date, ip) for a countable event-page view, else None."""
    if "GET /events/" not in line:
        return None
    match = _LINE_RE.match(line)
    if match is None or match["status"] != "200":
        return None
    ua = match["ua"].strip()
    if not ua or ua == "-" or _BOT_UA_RE.search(ua):
        return None
    try:
        day = datetime.strptime(match["day"], "%d/%b/%Y").date()
    except ValueError:
        return None
    return int(match["event_id"]), day, match["ip"]


def collect_event_day_counts(files, valid_dates):
    """{(event_id, date): (hits, distinct_ip_count)} over the given logs.

    Distinct-IP sets are held in memory per (event, day); bounded by the
    site's modest traffic over the ~60-day window.
    """
    counts = {}
    for line in _iter_filtered_lines(files, valid_dates):
        parsed = _parse_line(line)
        if parsed is None:
            continue
        event_id, day, ip = parsed
        entry = counts.setdefault((event_id, day), [0, set()])
        entry[0] += 1
        entry[1].add(ip)
    return {key: (hits, len(ips)) for key, (hits, ips) in counts.items()}


def persist_event_day_counts(counts, chunk_size=500):
    """Upsert per-(event, day) rows, keeping the MAX of stored and new values
    so repeated runs over the same (possibly partial) log window never shrink
    or inflate counts. Requires an app context."""
    rows = [
        {"event_id": event_id, "date": day, "hits": hits, "visitors": visitors}
        for (event_id, day), (hits, visitors) in sorted(counts.items())
    ]
    for start in range(0, len(rows), chunk_size):
        chunk = rows[start : start + chunk_size]
        stmt = insert(EventDailyViews).values(chunk)
        stmt = stmt.on_conflict_do_update(
            index_elements=["event_id", "date"],
            set_={
                "hits": func.max(EventDailyViews.hits, stmt.excluded.hits),
                "visitors": func.max(
                    EventDailyViews.visitors, stmt.excluded.visitors
                ),
            },
        )
        db.session.execute(stmt)
    db.session.commit()


def generate_event_view_stats() -> tuple[bool, str]:
    """Recompute and persist event view counts for the analytics window.
    Mirrors analytics.generate_stats()'s (success, message) contract."""
    log_files = find_log_files()
    if not log_files:
        return False, "No nginx access log files found"

    today = datetime.now().date()
    valid_dates = _build_valid_dates(
        today - timedelta(days=2 * STATS_WINDOW_DAYS - 1), today
    )
    counts = collect_event_day_counts(log_files, valid_dates)
    if not counts:
        return True, "No event page views found in stats window"

    persist_event_day_counts(counts)
    return True, f"Event view counts updated for {len(counts)} event-day(s)"


def event_popularity(limit=25):
    """Events ranked by total recorded hits, most viewed first.

    Returns [{"event_id", "event" (None when deleted), "hits", "visitors"}].
    Visitors are sums of daily uniques, like the Traffic panel.
    """
    rows = (
        db.session.query(
            EventDailyViews.event_id,
            func.sum(EventDailyViews.hits).label("hits"),
            func.sum(EventDailyViews.visitors).label("visitors"),
        )
        .group_by(EventDailyViews.event_id)
        .order_by(func.sum(EventDailyViews.hits).desc())
        .limit(limit)
        .all()
    )
    events = {
        event.id: event
        for event in Event.query.options(joinedload(Event.venue)).filter(
            Event.id.in_([row.event_id for row in rows])
        )
    }
    return [
        {
            "event_id": row.event_id,
            "event": events.get(row.event_id),
            "hits": row.hits,
            "visitors": row.visitors,
        }
        for row in rows
    ]
