"""Scan the existing approval queue for possible duplicates and flag them.

The ingest dedup only runs at ingest time on strict sources (konzibot), so any
duplicates that were staged before that check existed — or that came in through
another source — sit unflagged in the web queue. This one-shot re-runs the same
same-day collision check (services.ingest_dedup) across the current-and-future
staged queue, regardless of source, and sets needs_review/review_reason on the
offenders so they drop out of the web queue and into the admin TUI's "Queue
duplicates" review screen. Past events are never scanned.

    uv run python scripts/scan_queue_duplicates.py --dry-run   # preview, no writes
    uv run python scripts/scan_queue_duplicates.py             # today..+2 months
    uv run python scripts/scan_queue_duplicates.py --no-date-cap  # all future rows
    uv run python scripts/scan_queue_duplicates.py --source konzibot

Idempotent: already-flagged rows are left alone, so re-running only picks up
rows that became duplicates since the last run. Safe to run on the server after
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
    find_konzibot_duplicates,
    is_strong_match,
)


@dataclass
class ScanResult:
    scanned: int = 0
    newly_flagged: int = 0
    already_flagged: int = 0
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
        if rec.needs_review:
            result.already_flagged += 1
            continue
        matches = [
            m
            for m in find_konzibot_duplicates(
                rec.start_date,
                rec.city,
                rec.venue_name,
                rec.title,
                exclude_scraped_id=rec.id,
            )
            if is_strong_match(m)
        ]
        if not matches:
            continue
        reason = describe_duplicates(matches)
        result.newly_flagged += 1
        result.flagged.append((rec.id, rec.title or "(untitled)", reason))
        if not dry_run:
            rec.needs_review = True
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

    for scraped_id, title, reason in result.flagged:
        print(f"  #{scraped_id} {title} -> {reason}")
    verb = "would flag" if args.dry_run else "flagged"
    print(
        f"{result.scanned} scanned; {verb} {result.newly_flagged}; "
        f"{result.already_flagged} already flagged."
    )


if __name__ == "__main__":
    main()
