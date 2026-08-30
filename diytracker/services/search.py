"""Free-text search over events and venues.

Deliberately plain SQL LIKE rather than SQLite FTS5: the dataset is a few
thousand rows, and an FTS virtual table cannot come out of `db.create_all()`
(there is no Alembic in this project — see CLAUDE.md), so it would need a
hand-written migration plus triggers to stay in sync with every write path.
If the corpus ever outgrows LIKE, that is the trade to revisit.

Multi-word queries AND their terms — each term must match somewhere — so
"punk bern" narrows to punk shows in Bern instead of returning both sets.
"""

from sqlalchemy import or_
from sqlalchemy.orm import joinedload

from diytracker.models import Event, Venue
from diytracker.services.events import upcoming_filter

# Long enough for a band plus a city, short enough that nobody can hand the
# database a kilobyte of LIKE patterns.
MAX_QUERY_LENGTH = 80
MAX_TERMS = 6
DEFAULT_LIMIT = 100


def normalise_query(raw):
    """Trimmed, length-capped query string; '' when there is nothing to search."""
    return " ".join((raw or "").split())[:MAX_QUERY_LENGTH].strip()


def _terms(query):
    return [term for term in query.split(" ") if term][:MAX_TERMS]


def _pattern(term):
    """LIKE pattern matching *term* anywhere, with wildcards neutralised.

    Without escaping, a query of "%" matches every row and "_" matches any
    single character — user input must not be able to steer the pattern.
    """
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def like_patterns(query):
    """Escaped LIKE patterns, one per term of an already-normalised query,
    for callers that AND them onto their own base query (claim-page search)."""
    return [_pattern(term) for term in _terms(query)]


def search_events(raw_query, include_past=False, limit=DEFAULT_LIMIT):
    """Events matching every term of *raw_query*, soonest first."""
    query = normalise_query(raw_query)
    if not query:
        return []

    q = Event.query.options(joinedload(Event.venue)).join(
        Venue, Event.venue_id == Venue.id
    )
    if not include_past:
        q = q.filter(upcoming_filter())

    for term in _terms(query):
        pattern = _pattern(term)
        q = q.filter(
            or_(
                Event.name.ilike(pattern, escape="\\"),
                Event.acts.ilike(pattern, escape="\\"),
                Event.description.ilike(pattern, escape="\\"),
                Event.genre.ilike(pattern, escape="\\"),
                Venue.name.ilike(pattern, escape="\\"),
                Venue.city.ilike(pattern, escape="\\"),
            )
        )

    order = Event.date.desc() if include_past else Event.date.asc()
    return q.order_by(order).limit(limit).all()


def search_venues(raw_query, limit=10):
    """Venues matching every term of *raw_query*, so searching a venue name
    surfaces the venue page alongside its shows."""
    query = normalise_query(raw_query)
    if not query:
        return []

    q = Venue.query
    for term in _terms(query):
        pattern = _pattern(term)
        q = q.filter(
            or_(
                Venue.name.ilike(pattern, escape="\\"),
                Venue.city.ilike(pattern, escape="\\"),
            )
        )
    return q.order_by(Venue.name.asc()).limit(limit).all()
