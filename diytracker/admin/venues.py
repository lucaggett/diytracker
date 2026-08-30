"""Venue logic for the admin tool: browse and edit venues, plus the dedup
scan/merge.

The old CLI interleaved scanning, printing, and input() in one loop;
here scan() returns a plan and merge_venue_group() applies one merge, so
the TUI can rescan after each apply (a merge can backfill a missing city
and reveal a new exact match, so plans go stale after every merge).

Nothing here ever returns `Venue.accessibility_token`. That link lets its
holder overwrite a venue's accessibility answers with no history, so it stays
out of admin listings and terminal scrollback exactly as it stays off public
pages (see CLAUDE.md) — callers get `has_token` and nothing more.
"""

from dataclasses import dataclass, field

from sqlalchemy import func, or_

from diytracker.admin.core import AdminError, require_schema
from diytracker.models import Event, Venue, VenueAccessibility, db
from diytracker.services.cache import bust_cache
from diytracker.services.search import like_patterns, normalise_query
from diytracker.services.venue import (
    find_dedup_candidates,
    merge_group,
    select_survivor,
)
from diytracker.utils import resolve_canton

EDITABLE_FIELDS = ("name", "address", "city", "canton", "plz", "coords")
REQUIRED_FIELDS = ("name", "city", "plz")  # nullable=False on the model


@dataclass
class VenueInfo:
    id: int
    summary: str


@dataclass
class VenueRow:
    id: int
    name: str
    city: str
    canton: str
    plz: str
    address: str
    coords: str
    n_events: int
    has_token: bool  # never the token itself
    a11y_updated: str  # "" when the venue has no accessibility record


@dataclass
class AutoGroup:
    survivor: VenueInfo
    losers: list


@dataclass
class PairCandidate:
    reason: str
    a: VenueInfo
    b: VenueInfo
    survivor_id: int
    loser_ids: list


@dataclass
class VenueScan:
    db_name: str
    n_venues: int
    auto_groups: list = field(default_factory=list)
    pairs: list = field(default_factory=list)


@dataclass
class MergeResult:
    backfilled: list  # (field, value, source_id)
    accessibility: list  # notes
    warnings: list
    events_repointed: int
    venues_deleted: int


def venue_summary(venue, n_events):
    plz = venue.plz.strip() if venue.plz else ""
    parts = [
        f'#{venue.id} "{venue.name}"',
        venue.city.strip() if venue.city and venue.city.strip() else "(no city)",
        plz or "(no plz)",
    ]
    if venue.address and venue.address.strip():
        parts.append(venue.address.strip())
    if venue.coords and venue.coords.strip():
        parts.append("coords")
    if venue.accessibility_token:
        parts.append("token")
    parts.append(f"{n_events} event{'' if n_events == 1 else 's'}")
    return " · ".join(parts)


def _event_counts():
    return dict(
        db.session.query(Event.venue_id, func.count(Event.id))
        .group_by(Event.venue_id)
        .all()
    )


def _check_schema():
    # create_all() only creates missing tables — it never adds columns to
    # existing ones. On a DB that predates newer model fields the dedup
    # queries would die mid-run with OperationalError, so refuse to start.
    require_schema(
        db,
        (Venue, Event, VenueAccessibility),
        "Add the missing column(s) with a one-shot ALTER TABLE against "
        "instance/events.db, then rerun dedup — this project has no Alembic "
        "and keeps no migrations directory between releases (see CLAUDE.md).",
    )


def _venue_row(venue, n_events, a11y_updated):
    return VenueRow(
        id=venue.id,
        name=venue.name or "",
        city=(venue.city or "").strip(),
        canton=venue.canton or "",
        plz=(venue.plz or "").strip(),
        address=(venue.address or "").strip(),
        coords=(venue.coords or "").strip(),
        n_events=n_events,
        has_token=bool(venue.accessibility_token),
        a11y_updated=a11y_updated.strftime("%Y-%m-%d") if a11y_updated else "",
    )


def _a11y_dates():
    return dict(
        db.session.query(
            VenueAccessibility.venue_id, VenueAccessibility.updated_at
        ).all()
    )


def _get_venue(venue_id):
    venue = db.session.get(Venue, venue_id)
    if venue is None:
        raise AdminError(f"No venue with id {venue_id}.")
    return venue


def list_venues(search=None, limit=200):
    """Venues matching every term of *search* (name, city, canton, PLZ)."""
    query = Venue.query
    for pattern in like_patterns(normalise_query(search)):
        query = query.filter(
            or_(
                Venue.name.ilike(pattern, escape="\\"),
                Venue.city.ilike(pattern, escape="\\"),
                Venue.canton.ilike(pattern, escape="\\"),
                Venue.plz.ilike(pattern, escape="\\"),
            )
        )
    venues = query.order_by(Venue.name.asc()).limit(limit).all()
    counts = _event_counts()
    a11y = _a11y_dates()
    return [
        _venue_row(venue, counts.get(venue.id, 0), a11y.get(venue.id))
        for venue in venues
    ]


def get_venue(venue_id):
    venue = _get_venue(venue_id)
    counts = _event_counts()
    return _venue_row(venue, counts.get(venue.id, 0), _a11y_dates().get(venue.id))


def update_venue(venue_id, **fields):
    """Update any of EDITABLE_FIELDS on one venue; returns the fresh row.

    The canton goes through resolve_canton() like every other write path, so
    typing "Zürich" stores ZH rather than a value the canton pages can't
    filter on.
    """
    unknown = set(fields) - set(EDITABLE_FIELDS)
    if unknown:
        raise AdminError(f"Not an editable venue field: {', '.join(sorted(unknown))}.")
    venue = _get_venue(venue_id)

    values = {key: (value or "").strip() for key, value in fields.items()}
    for key in REQUIRED_FIELDS:
        if key in values and not values[key]:
            raise AdminError(f"{key.capitalize()} cannot be empty.")

    if "canton" in values:
        city = values.get("city", venue.city or "")
        values["canton"] = (resolve_canton(values["canton"], city) or "").upper()

    for key, value in values.items():
        # Optional columns store NULL rather than "" when cleared; the
        # required ones were rejected above if empty.
        setattr(venue, key, value if key in REQUIRED_FIELDS else (value or None))
    db.session.commit()
    # Venue name/city/canton are rendered on cached public pages and decide
    # which canton directory the venue lands in.
    bust_cache()
    return get_venue(venue_id)


def delete_venue(venue_id):
    """Delete a venue that has no events left, and its accessibility record.

    Refuses while events point at it: deleting then would either orphan them
    or take real listings off the calendar. Merging is what that case wants.
    """
    venue = _get_venue(venue_id)
    n_events = _event_counts().get(venue.id, 0)
    if n_events:
        raise AdminError(
            f"Venue #{venue.id} still has {n_events} event(s). "
            "Merge it in Venue dedup, or repoint the events first."
        )
    name = venue.name
    VenueAccessibility.query.filter_by(venue_id=venue.id).delete()
    db.session.delete(venue)
    db.session.commit()
    bust_cache()
    return name


def scan():
    """One pass over all venues: exact-duplicate groups that merge
    automatically, plus pairs needing human confirmation."""
    _check_schema()
    counts = _event_counts()
    auto_groups, pairs = find_dedup_candidates(Venue.query.all())
    result = VenueScan(db_name=db.engine.url.database, n_venues=Venue.query.count())
    for group in auto_groups:
        survivor, losers = select_survivor(group, counts)
        result.auto_groups.append(
            AutoGroup(
                survivor=VenueInfo(
                    survivor.id, venue_summary(survivor, counts.get(survivor.id, 0))
                ),
                losers=[
                    VenueInfo(loser.id, venue_summary(loser, counts.get(loser.id, 0)))
                    for loser in losers
                ],
            )
        )
    for venue_a, venue_b, reason in pairs:
        survivor, losers = select_survivor([venue_a, venue_b], counts)
        result.pairs.append(
            PairCandidate(
                reason=reason,
                a=VenueInfo(
                    venue_a.id, venue_summary(venue_a, counts.get(venue_a.id, 0))
                ),
                b=VenueInfo(
                    venue_b.id, venue_summary(venue_b, counts.get(venue_b.id, 0))
                ),
                survivor_id=survivor.id,
                loser_ids=[loser.id for loser in losers],
            )
        )
    return result


def merge_venue_group(survivor_id, loser_ids):
    """Merge losers into the survivor (fresh rows by id). Returns a
    MergeResult; raises AdminError if any venue vanished (stale plan)."""
    survivor = db.session.get(Venue, survivor_id)
    losers = [db.session.get(Venue, loser_id) for loser_id in loser_ids]
    if survivor is None or any(loser is None for loser in losers):
        raise AdminError("Venue already merged away — rescan and retry.")
    stats = merge_group(survivor, losers)
    db.session.commit()
    bust_cache()
    return MergeResult(
        backfilled=stats["backfilled"],
        accessibility=stats["accessibility"],
        warnings=stats["warnings"],
        events_repointed=stats["events_repointed"],
        venues_deleted=stats["venues_deleted"],
    )
