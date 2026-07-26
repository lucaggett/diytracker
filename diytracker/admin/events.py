"""Event management logic: list/status/delete plus event & genre dedup."""

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import or_

from diytracker.admin.core import AdminError
from diytracker.services.audit import record
from diytracker.models import Event, Submitter, Venue, db
from diytracker.services.cache import bust_cache
from diytracker.services.search import like_patterns, normalise_query
from diytracker.services.events import detach_scrape_approvals
from diytracker.services.event_dedup import (
    find_event_dedup_candidates,
    merge_events,
    select_survivor,
)
from diytracker.services.genre_dedup import (
    find_genre_dedup_groups,
    merge_genre_tokens,
    pick_canonical,
)

EVENT_STATUSES = ("scheduled", "cancelled", "postponed")


@dataclass
class EventRow:
    id: int
    date: str  # YYYY-MM-DD
    status: str
    name: str
    venue: str
    submitter: str


@dataclass
class EventDedupPair:
    a: EventRow
    b: EventRow
    shared: list


@dataclass
class GenreDedupPlan:
    db_name: str
    n_events: int
    # [(variants description, canonical)] for display
    groups: list = field(default_factory=list)
    # {wrong spelling: canonical} to apply
    mapping: dict = field(default_factory=dict)


def _row(event):
    return EventRow(
        id=event.id,
        date=f"{event.date:%Y-%m-%d}",
        status=event.status,
        name=event.name,
        venue=event.venue.name if event.venue else "(no venue)",
        submitter=event.submitter.email if event.submitter else "-",
    )


def _get_event(event_id):
    event = db.session.get(Event, event_id)
    if not event:
        raise AdminError(f"No event found with id {event_id}.")
    return event


def list_events(include_past=False, limit=20, search=None):
    """Events, soonest first. *search* ANDs its terms across name, acts,
    genre, venue and submitter — same dialect as the site search."""
    q = Event.query
    patterns = like_patterns(normalise_query(search))
    if patterns:
        q = q.outerjoin(Venue, Event.venue_id == Venue.id).outerjoin(
            Submitter, Event.submitter_id == Submitter.id
        )
        for pattern in patterns:
            q = q.filter(
                or_(
                    Event.name.ilike(pattern, escape="\\"),
                    Event.acts.ilike(pattern, escape="\\"),
                    Event.genre.ilike(pattern, escape="\\"),
                    Venue.name.ilike(pattern, escape="\\"),
                    Venue.city.ilike(pattern, escape="\\"),
                    Submitter.email.ilike(pattern, escape="\\"),
                )
            )
    if include_past:
        q = q.order_by(Event.date.desc())
    else:
        q = q.filter(Event.date >= datetime.now()).order_by(Event.date.asc())
    if limit:
        q = q.limit(limit)
    return [_row(e) for e in q.all()]


def get_event(event_id):
    return _row(_get_event(event_id))


def set_status(event_id, status):
    if status not in EVENT_STATUSES:
        raise AdminError(f"Unknown status {status!r} (expected {EVENT_STATUSES}).")
    event = _get_event(event_id)
    event.status = status
    record(
        "event.status",
        "event",
        event.id,
        actor="tui",
        detail=f"name={event.name!r} status={status}",
    )
    db.session.commit()
    bust_cache()
    return _row(event)


def delete_event(event_id):
    event = _get_event(event_id)
    row = _row(event)
    detach_scrape_approvals(event.id)
    record(
        "event.delete",
        "event",
        event.id,
        actor="tui",
        detail=f"name={event.name!r} date={event.date}",
    )
    db.session.delete(event)
    db.session.commit()
    bust_cache()
    return row


def scan_event_dups(include_past=False):
    """(n_scanned, [EventDedupPair]) for events whose names share 3+ words
    on the same date."""
    q = Event.query
    if not include_past:
        q = q.filter(Event.date >= datetime.now())
    events = q.all()
    pairs = find_event_dedup_candidates(events)
    return len(events), [
        EventDedupPair(a=_row(a), b=_row(b), shared=sorted(shared))
        for a, b, shared in pairs
    ]


def merge_event_pair(id_a, id_b):
    """Merge the pair, keeping the richer event. Returns (survivor_row,
    n_deleted); None if either side was already merged away this run."""
    event_a = db.session.get(Event, id_a)
    event_b = db.session.get(Event, id_b)
    if event_a is None or event_b is None:
        return None
    survivor, losers = select_survivor([event_a, event_b])
    stats = merge_events(survivor, losers)
    db.session.commit()
    bust_cache()
    return _row(survivor), stats["events_deleted"]


def scan_genre_dups():
    """Plan for normalizing genre spellings that differ only by
    case/whitespace."""
    events = Event.query.all()
    plan = GenreDedupPlan(db_name=db.engine.url.database, n_events=len(events))
    for _key, spellings in find_genre_dedup_groups(events).items():
        canonical = pick_canonical(spellings)
        variants = ", ".join(
            f"{spelling!r} x{count}" for spelling, count in spellings.items()
        )
        plan.groups.append((variants, canonical))
        for spelling in spellings:
            if spelling != canonical:
                plan.mapping[spelling] = canonical
    return plan


def apply_genre_merge(mapping):
    """Apply a scan_genre_dups() mapping; returns the changed-event count."""
    changed = merge_genre_tokens(Event.query.all(), mapping)
    db.session.commit()
    bust_cache()
    return changed
