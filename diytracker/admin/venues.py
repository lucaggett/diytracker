"""Venue dedup logic: scan for candidates, merge one group at a time.

The old CLI interleaved scanning, printing, and input() in one loop;
here scan() returns a plan and merge_venue_group() applies one merge, so
the TUI can rescan after each apply (a merge can backfill a missing city
and reveal a new exact match, so plans go stale after every merge).
"""

from dataclasses import dataclass, field

from sqlalchemy import func

from diytracker.admin.core import AdminError, require_schema
from diytracker.models import Event, Venue, VenueAccessibility, db
from diytracker.services.venue import (
    find_dedup_candidates,
    merge_group,
    select_survivor,
)


@dataclass
class VenueInfo:
    id: int
    summary: str


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
        "Run the scripts in migrations/ first "
        "(e.g. migrate_add_seo_columns.py), then rerun dedup.",
    )


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
                a=VenueInfo(venue_a.id, venue_summary(venue_a, counts.get(venue_a.id, 0))),
                b=VenueInfo(venue_b.id, venue_summary(venue_b, counts.get(venue_b.id, 0))),
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
    return MergeResult(
        backfilled=stats["backfilled"],
        accessibility=stats["accessibility"],
        warnings=stats["warnings"],
        events_repointed=stats["events_repointed"],
        venues_deleted=stats["venues_deleted"],
    )
