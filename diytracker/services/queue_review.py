"""Review logic for staged events flagged as possible duplicates.

The ingest dedup (services/ingest_dedup.py) marks colliding ScrapedEvent rows
``needs_review=True`` + a ``review_reason``. Those rows are hidden from the web
approval queue and triaged here instead. Two actions:

- ``discard`` — it really is a duplicate: drop it from the queue (mark approved
  without creating an Event, mirroring blueprints.submissions.delete_scraped_event).
- ``unflag`` — false positive: clear the flag so it reappears in the web queue
  for the normal approve/edit flow.

``review_reason`` is a snapshot taken at ingest time, so it goes stale as soon
as the colliding event is edited or deleted, and it carries no ids to link to.
``list_flagged`` therefore re-runs the matcher and returns live ``DupMatch``
rows alongside it; the stored string stays as the fallback for when nothing
collides any more (which is itself the answer: unflag it).

Plain functions returning dataclasses (never ORM objects) and raising
AdminError, so the route above decides how the failure surfaces.
"""

from dataclasses import dataclass, field
from datetime import datetime

from diytracker.services.errors import AdminError
from diytracker.models import ScrapedEvent, db
from diytracker.services.audit import record
from diytracker.services.ingest_dedup import find_konzibot_duplicates, is_strong_match


@dataclass
class DupMatch:
    """One record the staged row collides with, live as of this call."""

    kind: str  # "calendar" -> Event.id, "queue" -> ScrapedEvent.id
    id: int
    title: str
    venue: str
    city: str
    date: str  # YYYY-MM-DD, or "" when the record has no date
    time: str  # HH:MM, or "" when none is recorded
    signals: list  # which of city/venue/title matched
    strong: bool  # a real duplicate, not just the same night in the same city
    label: str


@dataclass
class QueuedDupRow:
    id: int
    source: str
    date: str  # YYYY-MM-DD, or "" when the row has no start_date
    title: str
    venue: str
    city: str
    review_reason: str
    time: str = ""
    url: str = ""
    flyer: str = ""
    matches: list = field(default_factory=list)


def _fmt_date(value):
    return f"{value:%Y-%m-%d}" if value else ""


def _fmt_time(value):
    return f"{value:%H:%M}" if value else ""


def _match_row(m):
    return DupMatch(
        kind=m["kind"],
        id=m["id"],
        title=m["title"],
        venue=m["venue"],
        city=m["city"],
        date=_fmt_date(m["date"]),
        time=_fmt_time(m["time"]),
        signals=m["signals"],
        strong=is_strong_match(m),
        label=m["label"],
    )


def _row(rec, matches=None):
    return QueuedDupRow(
        id=rec.id,
        source=rec.source or "-",
        date=_fmt_date(rec.start_date),
        title=rec.title or "(untitled)",
        venue=rec.venue_name or "-",
        city=rec.city or "-",
        review_reason=rec.review_reason or "",
        time=_fmt_time(rec.doors_open or rec.start_time),
        url=rec.url or "",
        flyer=rec.flyer or "",
        matches=matches or [],
    )


def _get_flagged(scraped_id):
    rec = db.session.get(ScrapedEvent, scraped_id)
    if rec is None:
        raise AdminError(f"No queued event found with id {scraped_id}.")
    if rec.status != ScrapedEvent.STATUS_PENDING:
        raise AdminError(f"Queued event #{scraped_id} is already resolved.")
    return rec


def list_flagged():
    """Flagged, still-unapproved queue rows, soonest first, each with the
    records it currently collides with (strong matches first).

    Re-matching costs two queries per row. Flagged sets run to tens, not
    thousands — the whole point is that an admin reads every one — so this
    stays a plain loop rather than a batched join.
    """
    rows = (
        ScrapedEvent.query.filter(
            ScrapedEvent.status == ScrapedEvent.STATUS_PENDING,
            ScrapedEvent.needs_review.is_(True),
        )
        .order_by(ScrapedEvent.start_date.asc())
        .all()
    )
    out = []
    for rec in rows:
        matches = [
            _match_row(m)
            for m in find_konzibot_duplicates(
                rec.start_date,
                rec.city,
                rec.venue_name,
                rec.title,
                exclude_scraped_id=rec.id,
            )
        ]
        matches.sort(key=lambda m: not m.strong)
        out.append(_row(rec, matches))
    return out


def _discard(rec, actor):
    rec.status = ScrapedEvent.STATUS_REJECTED
    rec.approved_at = datetime.now()
    record(
        "queue.reject",
        "scraped_event",
        rec.id,
        actor=actor,
        detail=f"duplicate discard: title={rec.title!r} reason={rec.review_reason!r}",
    )


def _unflag(rec, actor):
    rec.needs_review = False
    record(
        "queue.unflag",
        "scraped_event",
        rec.id,
        actor=actor,
        detail=f"title={rec.title!r}",
    )


def discard(scraped_id, actor="tui"):
    """Confirmed duplicate: drop it from the queue without publishing."""
    rec = _get_flagged(scraped_id)
    row = _row(rec)
    _discard(rec, actor)
    db.session.commit()
    return row


def unflag(scraped_id, actor="tui"):
    """False positive: clear the flag so it returns to the web queue."""
    rec = _get_flagged(scraped_id)
    _unflag(rec, actor)
    db.session.commit()
    return _row(rec)


def _resolve_many(scraped_ids, actor, apply):
    """Apply one action to every id that is still a pending flagged row.

    Ids that are gone or already resolved are skipped rather than raising: the
    checkboxes come from a page that may have been open a while, and one stale
    row must not abort the rest of the batch. Returns how many were resolved.
    """
    ids = [int(i) for i in scraped_ids]
    rows = ScrapedEvent.query.filter(
        ScrapedEvent.id.in_(ids),
        ScrapedEvent.status == ScrapedEvent.STATUS_PENDING,
        ScrapedEvent.needs_review.is_(True),
    ).all()
    for rec in rows:
        apply(rec, actor)
    db.session.commit()
    return len(rows)


def discard_many(scraped_ids, actor="tui"):
    """Discard every flagged row in ``scraped_ids``. Returns the count."""
    return _resolve_many(scraped_ids, actor, _discard)


def unflag_many(scraped_ids, actor="tui"):
    """Unflag every flagged row in ``scraped_ids``. Returns the count."""
    return _resolve_many(scraped_ids, actor, _unflag)
