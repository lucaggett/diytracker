"""Review logic for staged events flagged as possible duplicates.

The ingest dedup (services/ingest_dedup.py) marks colliding ScrapedEvent rows
``needs_review=True`` + a ``review_reason``. Those rows are hidden from the web
approval queue and triaged here instead. Two actions:

- ``discard`` — it really is a duplicate: drop it from the queue (mark approved
  without creating an Event, mirroring blueprints.submissions.delete_scraped_event).
- ``unflag`` — false positive: clear the flag so it reappears in the web queue
  for the normal approve/edit flow.

Plain functions returning dataclasses (never ORM objects) and raising
AdminError, shared by the TUI and CLI like the other admin modules.
"""

from dataclasses import dataclass
from datetime import datetime

from diytracker.admin.core import AdminError
from diytracker.models import ScrapedEvent, db


@dataclass
class QueuedDupRow:
    id: int
    source: str
    date: str  # YYYY-MM-DD, or "" when the row has no start_date
    title: str
    venue: str
    city: str
    review_reason: str


def _row(rec):
    return QueuedDupRow(
        id=rec.id,
        source=rec.source or "-",
        date=f"{rec.start_date:%Y-%m-%d}" if rec.start_date else "",
        title=rec.title or "(untitled)",
        venue=rec.venue_name or "-",
        city=rec.city or "-",
        review_reason=rec.review_reason or "",
    )


def _get_flagged(scraped_id):
    rec = db.session.get(ScrapedEvent, scraped_id)
    if rec is None:
        raise AdminError(f"No queued event found with id {scraped_id}.")
    if rec.approved:
        raise AdminError(f"Queued event #{scraped_id} is already resolved.")
    return rec


def list_flagged():
    """Flagged, still-unapproved queue rows, soonest first."""
    rows = (
        ScrapedEvent.query.filter(
            ScrapedEvent.approved.is_(False),
            ScrapedEvent.needs_review.is_(True),
        )
        .order_by(ScrapedEvent.start_date.asc())
        .all()
    )
    return [_row(rec) for rec in rows]


def discard(scraped_id):
    """Confirmed duplicate: drop it from the queue without publishing."""
    rec = _get_flagged(scraped_id)
    row = _row(rec)
    rec.approved = True
    rec.approved_at = datetime.now()
    db.session.commit()
    return row


def unflag(scraped_id):
    """False positive: clear the flag so it returns to the web queue."""
    rec = _get_flagged(scraped_id)
    rec.needs_review = False
    db.session.commit()
    return _row(rec)
