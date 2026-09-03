"""Genre landing pages: group upcoming events by parent genre.

Events carry the indexed comma-sentinel parent_genres column (",Metal,Punk,"),
kept in sync by ORM hooks, so the directory only needs one pass over upcoming
events. "Other" is excluded on purpose: it is a catch-all, not a genre anyone
searches for, and would make a thin, incoherent page. Only genres with at
least one upcoming event get a landing page.
"""

from diytracker.models import Event, Venue, db
from diytracker.services.cache import cache
from diytracker.services.events import upcoming_filter
from diytracker.services.seo import slugify
from diytracker.utils import CANTONS, PARENT_GENRES_ORDER, resolve_canton

# slug -> canonical parent genre name (metal, goth-industrial, hip-hop, ...).
GENRE_SLUGS = {slugify(g): g for g in PARENT_GENRES_ORDER if g != "Other"}


@cache.memoize()
def genre_directory():
    """{slug: {"name", "event_count", "venue_ids", "canton_slugs"}} for every
    parent genre (except Other) with an upcoming event. Venues and cantons are
    resolved the same way as canton_directory(); venues whose canton resolves
    to nothing still count for the genre but add no canton link. Cached;
    bust_cache() on any event/venue write invalidates it.
    """
    rows = (
        db.session.query(Event.parent_genres, Venue.id, Venue.canton, Venue.city)
        .join(Venue, Event.venue_id == Venue.id)
        .filter(upcoming_filter())
        .all()
    )

    by_name = {}
    for parents, venue_id, canton, city in rows:
        canton_slug = None
        code = resolve_canton(canton, city)
        canton_name = CANTONS.get(code)
        if canton_name:
            canton_slug = slugify(canton_name)
        for name in (parents or "").strip(",").split(","):
            if name == "Other" or not name:
                continue
            entry = by_name.setdefault(
                name,
                {
                    "name": name,
                    "event_count": 0,
                    "venue_ids": set(),
                    "canton_slugs": set(),
                },
            )
            entry["event_count"] += 1
            entry["venue_ids"].add(venue_id)
            if canton_slug:
                entry["canton_slugs"].add(canton_slug)

    return {
        slug: by_name[name] for slug, name in GENRE_SLUGS.items() if name in by_name
    }


def top_genres(limit=6):
    """(slug, name) pairs for the footer, most upcoming events first."""
    directory = genre_directory()
    ranked = sorted(
        directory.items(), key=lambda item: (-item[1]["event_count"], item[0])
    )
    return [(slug, info["name"]) for slug, info in ranked[:limit]]
