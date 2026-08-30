"""Routine operations that used to need a second terminal: cache purging and
scrape control.

Both are things the deploy notes tell you to do by hand — `rm -rf
instance/cache/*` after anything template-shaped, and "check whether the
scraper actually ran" — so they belong next to the rest of the admin logic
rather than in muscle memory.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from diytracker.admin.core import AdminError, require_schema
from diytracker.models import ScrapedEvent, SkippedUrl, db
from diytracker.paths import CACHE_DIR
from diytracker.services.cache import bust_cache
from diytracker.services.scraper import (
    SCRAPE_INTERVAL_HOURS,
    get_last_scrape_time,
    is_running,
    run_scrape_now,
)

RECENT_SKIPPED = 10


@dataclass
class PurgeResult:
    files_removed: int
    cache_dir: str


@dataclass
class ScrapeStatus:
    last_scrape: str  # "never" when the marker file is missing
    last_ago: str
    next_due: str
    running: bool
    interval_hours: int
    pending_queue: int
    flagged_queue: int
    published: int
    rejected: int
    recent_skipped: list = field(default_factory=list)  # (url, source, reason)


@dataclass
class ScrapeRun:
    created: int
    duplicate: int
    invalid: int
    skipped: int


def purge_cache():
    """Empty the per-locale page cache. Counts first, because flask-caching's
    clear() reports nothing and "it did something" is the whole feedback."""
    files = (
        [p for p in CACHE_DIR.glob("*") if p.is_file()] if CACHE_DIR.is_dir() else []
    )
    bust_cache()
    return PurgeResult(files_removed=len(files), cache_dir=str(CACHE_DIR))


def _fmt_delta(delta):
    minutes = int(delta.total_seconds() // 60)
    if minutes < 60:
        return f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {minutes}m"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours}h"


def scrape_status():
    """Where the scrape schedule stands, plus what the queue looks like.

    last_scrape.txt holds local wall-clock time, so it is compared against
    datetime.now() — not utcnow(), which would shift the answer by an hour or
    two depending on the season.
    """
    # create_all() never adds columns to an existing table, so a database
    # that missed a release's one-shot migration would die here with a raw
    # OperationalError the TUI can't render. Refuse with a readable message.
    require_schema(
        db,
        (ScrapedEvent, SkippedUrl),
        "Add the missing column(s) with a one-shot ALTER TABLE against "
        "instance/events.db, then retry.",
    )
    last = get_last_scrape_time()
    now = datetime.now()
    if last is None:
        last_label, ago, next_due = "never", "—", "on next start"
    else:
        last_label = last.strftime("%Y-%m-%d %H:%M")
        ago = _fmt_delta(now - last) + " ago"
        due = last + timedelta(hours=SCRAPE_INTERVAL_HOURS)
        next_due = f"in {_fmt_delta(due - now)}" if due > now else "due now"

    counts = dict(
        db.session.query(ScrapedEvent.status, db.func.count(ScrapedEvent.id))
        .group_by(ScrapedEvent.status)
        .all()
    )
    flagged = ScrapedEvent.query.filter_by(
        status=ScrapedEvent.STATUS_PENDING, needs_review=True
    ).count()
    skipped = (
        SkippedUrl.query.order_by(SkippedUrl.id.desc()).limit(RECENT_SKIPPED).all()
    )
    return ScrapeStatus(
        last_scrape=last_label,
        last_ago=ago,
        next_due=next_due,
        running=is_running(),
        interval_hours=SCRAPE_INTERVAL_HOURS,
        pending_queue=counts.get(ScrapedEvent.STATUS_PENDING, 0),
        flagged_queue=flagged,
        published=counts.get(ScrapedEvent.STATUS_PUBLISHED, 0),
        rejected=counts.get(ScrapedEvent.STATUS_REJECTED, 0),
        recent_skipped=[
            (row.url, row.source or "", row.reason or "") for row in skipped
        ],
    )


def run_scrape(app):
    """Scrape now, synchronously. Minutes, not seconds — callers run it off
    the UI thread."""
    started, counts = run_scrape_now(app)
    if not started:
        raise AdminError("A scrape is already running in this process.")
    if counts is None:
        raise AdminError(
            "The scrape failed; see logs/scrape_events.log and the error log."
        )
    return ScrapeRun(
        created=counts.get("created", 0),
        duplicate=counts.get("duplicate", 0),
        invalid=counts.get("invalid", 0),
        skipped=counts.get("skipped", 0),
    )
