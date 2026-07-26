"""Aggressive duplicate detection for messy push sources (konzibot).

The unified ingest core dedupes by canonical URL or (source, source_id). That
misses the case konzibot keeps hitting: the *same show* pushed with a fresh
id, so it lands as a new queue entry alongside the copy already on the calendar
or in the queue. This module flags those collisions on the way in.

Matching is deliberately loose: konzibot mangles cantons, venue spellings and
titles, so we normalize hard (fold diacritics, strip PLZ/canton, collapse
whitespace — reusing services.venue) and fuzzy-compare. Same date is the
anchor; a single further signal (city, venue or title) is enough to flag. The
row is still ingested — it just carries needs_review/review_reason so an admin
looks twice before approving.
"""

from datetime import datetime, time as time_type, timedelta
from difflib import SequenceMatcher

from diytracker.models import Event, ScrapedEvent
from diytracker.services.venue import normalize_city, normalize_name

_FUZZY_THRESHOLD = 0.82
_MIN_CONTAINMENT = 4
_REASON_CAP = 300


def _similar(a, b):
    """True when two normalized strings are the same, one contains the other
    (both non-trivial), or they're fuzzily close."""
    if not a or not b:
        return False
    if a == b:
        return True
    if len(a) >= _MIN_CONTAINMENT and len(b) >= _MIN_CONTAINMENT and (a in b or b in a):
        return True
    return SequenceMatcher(None, a, b).ratio() >= _FUZZY_THRESHOLD


def name_similarity(a, b):
    """Public fuzzy name compare for other callers (label claim suggestions):
    same normalization + matching rules as the konzibot dedup."""
    return _similar(normalize_name(a), normalize_name(b))


def _match_signals(city, venue, title, cand_city, cand_venue, cand_title):
    """Which of city/venue/title match between the payload and a candidate."""
    return {
        "city": _similar(normalize_city(city), normalize_city(cand_city)),
        "venue": _similar(normalize_name(venue), normalize_name(cand_venue)),
        "title": _similar(normalize_name(title), normalize_name(cand_title)),
    }


def find_konzibot_duplicates(
    start_date, city, venue_name, title, exclude_scraped_id=None
):
    """Return match dicts for calendar/queue events colliding with this one.

    A candidate collides when it shares the date and at least one of city /
    venue / title (normalized + fuzzy). Each match is
    {"kind", "label", "signals"}; an empty list means no collision.

    At ingest time the row being checked isn't in the session yet, so it can't
    self-match. When re-scanning rows that are already staged (the queue-dedup
    script) pass their id as ``exclude_scraped_id`` to keep them from matching
    themselves.
    """
    if start_date is None:
        return []
    city, venue_name, title = city or "", venue_name or "", title or ""
    matches = []

    # Calendar: Event.date is local wall-clock, so bound the day naively.
    day_start = datetime.combine(start_date, time_type.min)
    day_end = day_start + timedelta(days=1)
    for event in Event.query.filter(
        Event.date >= day_start, Event.date < day_end
    ).all():
        venue = event.venue
        signals = _match_signals(
            city,
            venue_name,
            title,
            venue.city if venue else "",
            venue.name if venue else "",
            event.name,
        )
        if any(signals.values()):
            where = venue.city if venue and venue.city else "?"
            matches.append(
                {
                    "kind": "calendar",
                    "label": f"{event.name} @ {where}",
                    "signals": [k for k, v in signals.items() if v],
                }
            )

    # Queue: other unapproved staged rows on the same date. The row being
    # ingested isn't in the session yet, so there's no self-match.
    queue_q = ScrapedEvent.query.filter(
        ScrapedEvent.approved.is_(False),
        ScrapedEvent.start_date == start_date,
    )
    if exclude_scraped_id is not None:
        queue_q = queue_q.filter(ScrapedEvent.id != exclude_scraped_id)
    for sc in queue_q.all():
        signals = _match_signals(
            city, venue_name, title, sc.city or "", sc.venue_name or "", sc.title or ""
        )
        if any(signals.values()):
            where = sc.city or "?"
            matches.append(
                {
                    "kind": "queue",
                    "label": f"{sc.title or '(untitled)'} @ {where}",
                    "signals": [k for k, v in signals.items() if v],
                }
            )
    return matches


def describe_duplicates(matches):
    """Human-readable review_reason for the queue, capped to the column size.

    Just the collided-with list — the queue template supplies the "possible
    duplicate" heading (and translates it), so this stays a plain summary.
    """
    if not matches:
        return None
    parts = []
    for m in matches:
        label = "Calendar" if m["kind"] == "calendar" else "Queue"
        parts.append(f"{label}: {m['label']}")
    return "; ".join(parts)[:_REASON_CAP]
