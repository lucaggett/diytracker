"""Label helpers: slug generation, form choices and the promoter dashboard
stats query."""

from datetime import date, timedelta

from sqlalchemy import func
from sqlalchemy.orm import joinedload

from diytracker.models import Event, EventDailyViews, Label, ScrapedEvent, db
from diytracker.services.ingest_dedup import name_similarity
from diytracker.services.seo import slugify


def unique_slug(name, exclude_id=None):
    """Slug for *name* that is unique among labels, suffixing -2/-3/... on
    collision. `exclude_id` skips the label being renamed."""
    base = slugify(name) or "label"
    slug = base
    counter = 2
    while True:
        query = Label.query.filter_by(slug=slug)
        if exclude_id is not None:
            query = query.filter(Label.id != exclude_id)
        if query.first() is None:
            return slug
        slug = f"{base}-{counter}"
        counter += 1


def all_label_choices():
    """(value, name) pairs for EventForm.label_id; "" means no label.

    Every label, so admin-only surfaces (admin edit_event) — that form is
    how misattributed labels get fixed. User-facing forms must use
    owned_label_choices() instead.
    """
    labels = Label.query.order_by(Label.name.asc()).all()
    return [("", "—")] + [(str(label.id), label.name) for label in labels]


def owned_labels(user):
    """The labels *user* may attach to events. Admins get no special
    treatment — attaching someone else's label is exactly the accident
    this prevents."""
    return Label.query.filter_by(promoter_id=user.id).order_by(Label.name.asc()).all()


def owned_label_choices(user):
    """(value, name) pairs for EventForm.label_id restricted to the user's
    own labels; "" means no label."""
    return [("", "—")] + [(str(label.id), label.name) for label in owned_labels(user)]


def likely_label_events(labels, events):
    """[(event, label)] pairs where a label's name fuzzily matches the event
    name or one of its acts — candidates for "likely yours" on the claim
    page. First matching label wins per event; events keep their order."""
    matches = []
    for event in events:
        acts = [act.strip() for act in (event.acts or "").split(",") if act.strip()]
        for label in labels:
            if name_similarity(label.name, event.name) or any(
                name_similarity(label.name, act) for act in acts
            ):
                matches.append((event, label))
                break
    return matches


TREND_DAYS = 30


def likely_queue_matches(labels):
    """Pending, upcoming queue rows whose title/line-up/organizer fuzzily
    matches one of *labels* — the read-only "in review, likely yours"
    dashboard panel. Returns [(ScrapedEvent, Label)]."""
    if not labels:
        return []
    rows = (
        ScrapedEvent.query.filter(
            ScrapedEvent.status == ScrapedEvent.STATUS_PENDING,
            ScrapedEvent.start_date >= date.today(),
        )
        .order_by(ScrapedEvent.start_date.asc())
        .all()
    )
    matches = []
    for row in rows:
        fields = [row.title, row.performers, row.organizer]
        for label in labels:
            if any(name_similarity(label.name, value) for value in fields if value):
                matches.append((row, label))
                break
    return matches


def label_stats(user):
    """Dashboard rows for a promoter: every event carrying one of their
    labels, newest event first, with accumulated page-view counts.

    Returns [{"event", "label", "hits", "visitors"}]. Counts mirror
    event_views.event_popularity(): sums over EventDailyViews, where
    event_id is deliberately not a foreign key — hence the dict lookup
    instead of a join.
    """
    labels = Label.query.filter_by(promoter_id=user.id).order_by(Label.name.asc()).all()
    if not labels:
        return []

    events = (
        Event.query.options(joinedload(Event.venue))
        .filter(Event.label_id.in_([label.id for label in labels]))
        .order_by(Event.date.desc())
        .all()
    )
    if not events:
        return []

    event_ids = [event.id for event in events]
    counts = {
        row.event_id: (row.hits, row.visitors)
        for row in db.session.query(
            EventDailyViews.event_id,
            func.sum(EventDailyViews.hits).label("hits"),
            func.sum(EventDailyViews.visitors).label("visitors"),
        )
        .filter(EventDailyViews.event_id.in_(event_ids))
        .group_by(EventDailyViews.event_id)
    }

    # Per-event daily series for the dashboard sparkline: one query for the
    # whole window, keyed (event_id, date) — same aggregation shape as the
    # TUI Traffic screen's events view.
    trend_start = date.today() - timedelta(days=TREND_DAYS - 1)
    daily = {
        (row.event_id, row.date): row.hits
        for row in db.session.query(
            EventDailyViews.event_id, EventDailyViews.date, EventDailyViews.hits
        ).filter(
            EventDailyViews.event_id.in_(event_ids),
            EventDailyViews.date >= trend_start,
        )
    }
    days = [trend_start + timedelta(days=i) for i in range(TREND_DAYS)]

    labels_by_id = {label.id: label for label in labels}
    return [
        {
            "event": event,
            "label": labels_by_id[event.label_id],
            "hits": counts.get(event.id, (0, 0))[0],
            "visitors": counts.get(event.id, (0, 0))[1],
            "trend": [daily.get((event.id, day), 0) for day in days],
        }
        for event in events
    ]
