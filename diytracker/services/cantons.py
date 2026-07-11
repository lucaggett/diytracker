"""Canton landing pages: group venues by canton, normalized at read time.

Venue.canton is nullable free text (2-letter codes from ingest, full names
in older data), so each venue is resolved through resolve_canton(), which
also falls back to inferring the canton from the city name. Venues that
resolve to nothing stay on the main calendar but get no canton page. Only
cantons with at least one upcoming event get a landing page.
"""

from datetime import datetime

from diytracker.models import Event, Venue, db
from diytracker.services.cache import cache
from diytracker.services.seo import slugify
from diytracker.utils import CANTONS, resolve_canton


@cache.memoize()
def canton_directory():
    """{slug: {"code", "name", "venue_ids", "event_count"}} for every canton
    with an upcoming event. Names are the canonical (untranslated) canton
    names from CANTONS; slugs derive from them. Cached; bust_cache() on any
    event/venue write invalidates it."""
    rows = (
        db.session.query(Venue.id, Venue.canton, Venue.city, db.func.count(Event.id))
        .join(Event, Event.venue_id == Venue.id)
        .filter(Event.date >= datetime.now())
        .group_by(Venue.id)
        .all()
    )

    directory = {}
    for venue_id, canton, city, count in rows:
        code = resolve_canton(canton, city)
        name = CANTONS.get(code)
        if name is None:
            continue
        entry = directory.setdefault(
            slugify(name),
            {"code": code, "name": name, "venue_ids": [], "event_count": 0},
        )
        entry["venue_ids"].append(venue_id)
        entry["event_count"] += count
    return directory


def top_cantons(limit=10):
    """(slug, canonical_name) pairs for the footer, most upcoming events
    first. Names are localized in the template via _()."""
    directory = canton_directory()
    ranked = sorted(
        directory.items(), key=lambda item: (-item[1]["event_count"], item[0])
    )
    return [(slug, info["name"]) for slug, info in ranked[:limit]]
