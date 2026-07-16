"""Statistics logic: contribution leaderboard and app overview."""

from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

from dateutil.relativedelta import relativedelta
from sqlalchemy import func

from diytracker.admin.core import fmt_size, require_schema
from diytracker.models import (
    Event,
    ScrapedEvent,
    SkippedUrl,
    Submitter,
    Venue,
    db,
    utcnow,
)

TIMEFRAMES = {
    "7d": "last 7 days",
    "month": "last month",
    "3months": "last 3 months",
    "all": "all time",
}


@dataclass
class LeaderboardResult:
    timeframe_label: str
    rows: list = field(default_factory=list)  # (email, count)
    unattributed: int = 0

    @property
    def total(self):
        return sum(count for _email, count in self.rows) + self.unattributed


@dataclass
class OverviewResult:
    db_name: str
    db_size: str  # formatted, or "" if the file is missing
    total_events: int
    upcoming_events: int
    n_venues: int
    n_users: int
    n_admins: int
    n_no_password: int
    scrape_queue: int
    skipped_urls: int


def _since(timeframe):
    """Start of the given timeframe as naive UTC, or None for all time."""
    now = utcnow()
    return {
        "7d": now - timedelta(days=7),
        "month": now - relativedelta(months=1),
        "3months": now - relativedelta(months=3),
        "all": None,
    }[timeframe]


def leaderboard(timeframe="all"):
    require_schema(
        db, (Event,), "Run migrations/migrate_add_created_at.py first."
    )
    since = _since(timeframe)
    ranked = (
        db.session.query(Submitter.email, func.count(Event.id).label("n"))
        .join(Event, Event.submitter_id == Submitter.id)
        .group_by(Submitter.id)
    )
    unattributed = Event.query.filter(Event.submitter_id.is_(None))
    if since is not None:
        ranked = ranked.filter(Event.created_at >= since)
        unattributed = unattributed.filter(Event.created_at >= since)
    rows = ranked.order_by(func.count(Event.id).desc(), Submitter.email).all()
    return LeaderboardResult(
        timeframe_label=TIMEFRAMES[timeframe],
        rows=[(email, count) for email, count in rows],
        unattributed=unattributed.count(),
    )


def overview():
    now = utcnow()
    total_events = Event.query.count()
    users = Submitter.query.all()
    db_file = Path(db.engine.url.database)
    return OverviewResult(
        db_name=db.engine.url.database,
        db_size=fmt_size(db_file.stat().st_size) if db_file.is_file() else "",
        total_events=total_events,
        upcoming_events=Event.query.filter(Event.date >= now).count(),
        n_venues=Venue.query.count(),
        n_users=len(users),
        n_admins=sum(1 for user in users if user.is_admin),
        n_no_password=sum(1 for user in users if not user.password_hash),
        scrape_queue=ScrapedEvent.query.filter(
            ScrapedEvent.approved.is_(False)
        ).count(),
        skipped_urls=SkippedUrl.query.count(),
    )
