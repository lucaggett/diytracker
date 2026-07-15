"""Distribution statistics over the whole event database (admin-only page).

Unlike cantons.py/genres.py these aggregate ALL events, past included — the
point is to see how the database (and the scene) is distributed over time,
genre, geography and price. Memoized like the other directories; the global
bust_cache() on every event/venue write invalidates them.
"""

import statistics as stats_module
from collections import Counter
from datetime import date

from dateutil.relativedelta import relativedelta

from diytracker.models import Event, Venue, db
from diytracker.services.cache import cache
from diytracker.services.seo import parse_price
from diytracker.utils import CANTONS, PARENT_GENRES_ORDER, resolve_canton

PRICE_BUCKETS = ((0, 10), (10, 20), (20, 30), (30, 50), (50, None))


@cache.memoize()
def events_per_month():
    """[("YYYY-MM", count)] over the full history, ascending."""
    rows = (
        db.session.query(db.func.strftime("%Y-%m", Event.date), db.func.count(Event.id))
        .group_by(db.func.strftime("%Y-%m", Event.date))
        .order_by(db.func.strftime("%Y-%m", Event.date).asc())
        .all()
    )
    return [(month, count) for month, count in rows]


def monthly_series(months=24):
    """The last `months` calendar months as [{"month", "count"}], zero-filled,
    oldest first — chart-ready, same shape as the admin traffic series."""
    counts = dict(events_per_month())
    first = date.today().replace(day=1) - relativedelta(months=months - 1)
    series = []
    for i in range(months):
        month = first + relativedelta(months=i)
        key = month.strftime("%Y-%m")
        series.append({"month": key, "count": counts.get(key, 0)})
    return series


def events_per_year():
    """[(year, count)] derived from events_per_month(), ascending."""
    totals = Counter()
    for month, count in events_per_month():
        totals[int(month[:4])] += count
    return sorted(totals.items())


@cache.memoize()
def events_by_parent_genre():
    """[(genre_name, count)] in PARENT_GENRES_ORDER; events with several
    parent genres count once per genre. Untagged events land in "Other"."""
    totals = Counter()
    for (parents,) in db.session.query(Event.parent_genres).all():
        names = [name for name in (parents or "").strip(",").split(",") if name]
        for name in names or ["Other"]:
            totals[name] += 1
    ordered = [(name, totals[name]) for name in PARENT_GENRES_ORDER if totals[name]]
    ordered += sorted(
        (name, count)
        for name, count in totals.items()
        if name not in PARENT_GENRES_ORDER
    )
    return ordered


@cache.memoize()
def events_by_canton():
    """[(canton_name_or_None, count)], most events first; None means the
    venue's canton could not be resolved."""
    rows = (
        db.session.query(Venue.canton, Venue.city, db.func.count(Event.id))
        .join(Event, Event.venue_id == Venue.id)
        .group_by(Venue.id)
        .all()
    )
    totals = Counter()
    for canton, city, count in rows:
        totals[CANTONS.get(resolve_canton(canton, city))] += count
    return sorted(totals.items(), key=lambda item: (-item[1], item[0] or "~"))


@cache.memoize()
def top_venues(limit=10):
    """[(venue, event_count)] over all events, busiest first."""
    return (
        db.session.query(Venue, db.func.count(Event.id).label("event_count"))
        .join(Event, Event.venue_id == Venue.id)
        .group_by(Venue.id)
        .order_by(db.func.count(Event.id).desc(), db.func.lower(Venue.name).asc())
        .limit(limit)
        .all()
    )


@cache.memoize()
def ticket_price_stats():
    """Approximate CHF price stats via seo.parse_price (free/Kollekte → 0,
    ranges → lower bound, unparseable → None). Aggregates (mean/median/…)
    and buckets cover paid events only; free and unparseable are counted
    separately."""
    prices = [parse_price(raw) for (raw,) in db.session.query(Event.ticket_price).all()]
    total = len(prices)
    unparsed = sum(1 for p in prices if p is None)
    free = sum(1 for p in prices if p == 0)
    paid = sorted(p for p in prices if p)

    buckets = []
    for low, high in PRICE_BUCKETS:
        label = f"{low}–{high}" if high is not None else f"{low}+"
        count = sum(1 for p in paid if p >= low and (high is None or p < high))
        buckets.append((label, count))

    return {
        "total": total,
        "free": free,
        "unparsed": unparsed,
        "paid": len(paid),
        "mean": round(stats_module.mean(paid), 2) if paid else None,
        "median": round(stats_module.median(paid), 2) if paid else None,
        "min": paid[0] if paid else None,
        "max": paid[-1] if paid else None,
        "buckets": buckets,
    }
