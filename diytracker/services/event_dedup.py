from diytracker.models import db, EventDailyViews, ScrapedEvent
from diytracker.services.venue import normalize_name

# The same show is sometimes entered twice (e.g. once scraped, once submitted
# by a promoter under a slightly different title). Sharing this many distinct
# name words on the same date is treated as a candidate worth a human look —
# always interactive, since name overlap alone doesn't prove duplication.
MIN_SHARED_WORDS = 3

_FILLED_FIELDS = ("ticket_link", "genre", "acts", "description", "source_url", "flyer")


def _name_words(name):
    return set(normalize_name(name).split())


def find_event_dedup_candidates(events):
    """Pair up events on the same date whose names share more than two words
    1:1. Returns a list of (event_a, event_b, shared_words) tuples, sorted by
    date then id, for interactive review."""
    by_date = {}
    for event in events:
        by_date.setdefault(event.date.date(), []).append(event)

    pairs = []
    for day, day_events in sorted(by_date.items()):
        day_events = sorted(day_events, key=lambda e: e.id)
        for i, event_a in enumerate(day_events):
            words_a = _name_words(event_a.name)
            for event_b in day_events[i + 1 :]:
                shared = words_a & _name_words(event_b.name)
                if len(shared) >= MIN_SHARED_WORDS:
                    pairs.append((event_a, event_b, shared))
    return pairs


def _filled(value):
    return bool((value or "").strip()) if isinstance(value, str) else bool(value)


def completeness_score(event):
    """How many optional fields are filled — used to pick merge survivors."""
    return sum(_filled(getattr(event, field)) for field in _FILLED_FIELDS)


def select_survivor(group):
    """Pick the event to keep: most filled fields, then oldest (lowest id)."""
    ranked = sorted(group, key=lambda e: (-completeness_score(e), e.id))
    return ranked[0], ranked[1:]


def merge_events(survivor, losers):
    """Fold `losers` into `survivor`: repoint any scrape-queue approval links,
    drop the losers' per-day view rows (their counts can't be folded into the
    survivor's without double-counting shared days), then delete the losers.
    Flushes but does not commit. Returns a stats dict describing what happened."""
    loser_ids = [loser.id for loser in losers]

    scraped_repointed = ScrapedEvent.query.filter(
        ScrapedEvent.approved_event_id.in_(loser_ids)
    ).update({"approved_event_id": survivor.id}, synchronize_session=False)

    views_dropped = EventDailyViews.query.filter(
        EventDailyViews.event_id.in_(loser_ids)
    ).delete(synchronize_session=False)

    for loser in losers:
        db.session.delete(loser)
    db.session.flush()

    return {
        "scraped_events_repointed": scraped_repointed,
        "views_dropped": views_dropped,
        "events_deleted": len(losers),
    }
