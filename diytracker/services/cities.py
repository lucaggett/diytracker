"""City landing pages: group free-text Venue.city values by slug.

Venue.city is free text with real-world mess ("GENÈVE" vs "Genève",
trailing spaces), so cities are grouped by their slug and the most common
raw spelling wins as the display name. Only cities with at least one
upcoming event get a landing page.
"""

from datetime import datetime

from diytracker.models import Event, Venue, db
from diytracker.services.cache import cache
from diytracker.services.seo import RESERVED_SLUGS, slugify


@cache.memoize()
def city_directory():
    """{slug: {"name": display_name, "raw_names": [raw city spellings]}}
    for every city with an upcoming event. Cached; bust_cache() on any
    event/venue write invalidates it."""
    rows = (
        db.session.query(Venue.city, db.func.count(Event.id))
        .join(Event, Event.venue_id == Venue.id)
        .filter(Event.date >= datetime.now())
        .group_by(Venue.city)
        .all()
    )

    directory = {}
    for raw_city, count in rows:
        display = " ".join((raw_city or "").split())
        slug = slugify(display)
        if not slug or slug in RESERVED_SLUGS:
            continue
        entry = directory.setdefault(
            slug, {"name": display, "raw_names": [], "event_count": 0, "_top": 0}
        )
        entry["raw_names"].append(raw_city)
        entry["event_count"] += count
        # Most frequent raw spelling becomes the display name.
        if count > entry["_top"]:
            entry["name"] = display
            entry["_top"] = count

    for entry in directory.values():
        del entry["_top"]
    return directory


def top_cities(limit=10):
    """(slug, display_name) pairs for the footer, most upcoming events first."""
    directory = city_directory()
    ranked = sorted(
        directory.items(), key=lambda item: (-item[1]["event_count"], item[0])
    )
    return [(slug, info["name"]) for slug, info in ranked[:limit]]
