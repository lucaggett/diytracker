"""Public archive of past events, grouped by month.

"Past" means strictly before the start of today — a stable boundary that
plays well with the page cache (an event tonight is not archived at 23:59).
Follows the cantons.py/genres.py pattern: memoized directory busted by the
global bust_cache() on every event/venue write.
"""

from collections import defaultdict
from datetime import date, datetime, time

from dateutil.relativedelta import relativedelta
from sqlalchemy.orm import joinedload

from diytracker.models import Event, db
from diytracker.services.cache import cache


def _today_start():
    return datetime.combine(date.today(), time.min)


@cache.memoize()
def archive_directory():
    """{year: [(month, event_count), ...]} for every month with at least one
    past event, newest first (years descending, months descending within)."""
    rows = (
        db.session.query(
            db.func.strftime("%Y", Event.date),
            db.func.strftime("%m", Event.date),
            db.func.count(Event.id),
        )
        .filter(Event.date < _today_start())
        .group_by(
            db.func.strftime("%Y", Event.date), db.func.strftime("%m", Event.date)
        )
        .order_by(
            db.func.strftime("%Y", Event.date).desc(),
            db.func.strftime("%m", Event.date).desc(),
        )
        .all()
    )
    directory = defaultdict(list)
    for year, month, count in rows:
        directory[int(year)].append((int(month), count))
    return dict(directory)


def archive_month_events(year, month):
    """Past events in the given month, grouped by day ({date: [events]},
    days ascending, events ascending within each day)."""
    start = datetime(year, month, 1)
    end = min(start + relativedelta(months=1), _today_start())
    events = (
        Event.query.options(joinedload(Event.venue))
        .filter(Event.date >= start, Event.date < end)
        .order_by(Event.date.asc())
        .all()
    )
    grouped = defaultdict(list)
    for event in events:
        grouped[event.date.date()].append(event)
    return dict(grouped)


def adjacent_months(year, month):
    """((prev_year, prev_month), (next_year, next_month)) among the months
    that actually have past events; None on either side when at the edge."""
    months = sorted(
        (y, m)
        for y, month_counts in archive_directory().items()
        for m, _count in month_counts
    )
    try:
        idx = months.index((year, month))
    except ValueError:
        return None, None
    prev_month = months[idx - 1] if idx > 0 else None
    next_month = months[idx + 1] if idx + 1 < len(months) else None
    return prev_month, next_month
