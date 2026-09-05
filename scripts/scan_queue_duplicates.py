"""Scan the existing approval queue for possible duplicates and flag them.

The dedup check runs when a row is ingested, so anything staged before that
check covered its source sits unflagged, and any row's collisions go stale as
the queue around it is approved or rejected. This re-runs the same same-day
check (services.ingest_dedup) across the current-and-future staged queue and
rewrites the verdict: a strong match (same title, or same venue and city) sets
needs_review and moves the row to /queue/duplicates, a weak one leaves it in the
queue with an inline notice, and a row that no longer collides is cleared.
Past events are never scanned.

    uv run python scripts/scan_queue_duplicates.py --dry-run   # preview, no writes
    uv run python scripts/scan_queue_duplicates.py             # today..+2 months
    uv run python scripts/scan_queue_duplicates.py --no-date-cap  # all future rows
    uv run python scripts/scan_queue_duplicates.py --source konzibot

Idempotent: the verdict is recomputed from the current queue every time, so
re-running converges rather than accumulating. Safe to run on the server after
a deploy to backfill the existing queue.
"""

import argparse
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime

from dateutil.relativedelta import relativedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from diytracker.app import create_app
from diytracker.models import ScrapedEvent, db
from diytracker.services.ingest_dedup import (
    describe_duplicates,
    find_duplicate_matches,
    has_strong_match,
)


@dataclass
class ScanResult:
    scanned: int = 0
    strong: int = 0  # pulled out of the queue for triage
    weak: int = 0  # stays in the queue behind an inline notice
    cleared: int = 0  # collided once, doesn't any more
    flagged: list = field(default_factory=list)  # [(id, title, reason)]


def scan_queue(source=None, date_from=None, date_to=None, dry_run=False):
    """Flag unapproved queue rows that collide with the calendar or each other.

    Returns a ScanResult. With dry_run=True nothing is written. Must run inside
    a Flask app context.
    """
    q = ScrapedEvent.query.filter(ScrapedEvent.status == ScrapedEvent.STATUS_PENDING)
    if source:
        q = q.filter(ScrapedEvent.source == source)
    if date_from is not None:
        q = q.filter(ScrapedEvent.start_date >= date_from)
    if date_to is not None:
        q = q.filter(ScrapedEvent.start_date <= date_to)
    rows = q.order_by(ScrapedEvent.start_date.asc()).all()

    result = ScanResult(scanned=len(rows))
    for rec in rows:
        matches = find_duplicate_matches(
            rec.start_date,
            rec.city,
            rec.venue_name,
            rec.title,
            exclude_scraped_id=rec.id,
        )
        reason = describe_duplicates(matches)
        strong = has_strong_match(matches)
        # Every row is re-evaluated, not just the unflagged ones: what a row
        # collides with changes as the queue around it is approved and rejected,
        # and a reason that has gone stale is worse than none.
        if reason is None:
            if rec.needs_review or rec.review_reason:
                result.cleared += 1
                if not dry_run:
                    rec.needs_review = False
                    rec.review_reason = None
            continue
        if strong:
            result.strong += 1
        else:
            result.weak += 1
        result.flagged.append(
            (
                rec.id,
                rec.title or "(untitled)",
                ("strong" if strong else "weak"),
                reason,
            )
        )
        if not dry_run:
            rec.needs_review = strong
            rec.review_reason = reason
    if not dry_run:
        db.session.commit()
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="report collisions without writing anything",
    )
    ap.add_argument(
        "--source", default=None, help="limit to one source (e.g. konzibot)"
    )
    ap.add_argument(
        "--no-date-cap",
        action="store_true",
        help="scan every current-or-future row, not just today..+2 months",
    )
    args = ap.parse_args()

    # Past events are never scanned — a duplicate of a show that already
    # happened isn't worth an admin's review. today is always the lower bound.
    today = datetime.now().date()
    date_from = today
    date_to = None if args.no_date_cap else today + relativedelta(months=2)

    app = create_app()
    with app.app_context():
        result = scan_queue(
            source=args.source,
            date_from=date_from,
            date_to=date_to,
            dry_run=args.dry_run,
        )

    for scraped_id, title, level, reason in result.flagged:
        print(f"  [{level}] #{scraped_id} {title} -> {reason}")
    verb = "would flag" if args.dry_run else "flagged"
    print(
        f"{result.scanned} scanned; {verb} {result.strong} for triage, "
        f"{result.weak} warned in place, {result.cleared} no longer colliding."
    )


if __name__ == "__main__":
    main()
