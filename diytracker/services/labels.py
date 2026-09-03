"""Label helpers: slug generation, form choices and the promoter dashboard
stats query.
"""

from datetime import date, timedelta

from sqlalchemy import func
from sqlalchemy.orm import joinedload

from diytracker.models import Event, EventDailyViews, Label, ScrapedEvent, db
from diytracker.services.ingest_dedup import name_similarity
from diytracker.services.seo import slugify


def unique_slug(name, exclude_id=None):
    """Slug for *name* that is unique among labels, suffixing -2/-3/... on
    collision. `exclude_id` skips the label being renamed.
    """
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
    this prevents.
    """
    return Label.query.filter_by(promoter_id=user.id).order_by(Label.name.asc()).all()


def owned_label_choices(user):
    """(value, name) pairs for EventForm.label_id restricted to the user's
    own labels; "" means no label.
    """
    return [("", "—")] + [(str(label.id), label.name) for label in owned_labels(user)]


def _first_matching_label(labels, name, acts_raw):
    """The first label in *labels* whose name fuzzily matches the event name
    or one of its acts, else None.
    """
    acts = [act.strip() for act in (acts_raw or "").split(",") if act.strip()]
    for label in labels:
        if name_similarity(label.name, name) or any(
            name_similarity(label.name, act) for act in acts
        ):
            return label
    return None


def suggest_label_events(labels, base_query):
    """[(event, label)] for the claim page's "likely yours" panel.

    The match itself only needs each event's name and line-up, so the scan
    runs over those two columns rather than hydrating every upcoming
    unlabelled event (with its venue) just to discard almost all of them;
    only the handful that actually match are then loaded in full. *base_query*
    is the unfiltered upcoming-unlabelled query — unfiltered on purpose, so a
    suggestion can't be hidden by the page's own search or pagination.
    """
    if not labels:
        return []
    rows = base_query.with_entities(Event.id, Event.name, Event.acts).all()
    hits = {}
    for event_id, name, acts in rows:
        label = _first_matching_label(labels, name, acts)
        if label is not None:
            hits[event_id] = label
    if not hits:
        return []
    events = (
        Event.query.options(joinedload(Event.venue))
        .filter(Event.id.in_(hits))
        .order_by(Event.date.asc())
        .all()
    )
    return [(event, hits[event.id]) for event in events]


TREND_DAYS = 30


def likely_queue_matches(labels):
    """Pending, upcoming queue rows whose title/line-up/organizer fuzzily
    matches one of *labels* — the read-only "in review, likely yours"
    dashboard panel. Returns [(ScrapedEvent, Label)].
    """
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
