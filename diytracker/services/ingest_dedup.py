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

Each match carries the matched record's id and its date/venue/city/title, so
the queue-duplicates page can link straight to the colliding show and put the
two side by side. Those fields are free — the ORM objects are already loaded
here — and additive: ``describe_duplicates`` reads only ``kind``/``label`` and
``scan_queue`` only ``signals``.
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


def is_strong_match(match):
    """A real duplicate — not just two different shows on the same night.

    The ingest heuristic flags on a *single* signal (same date + city OR venue
    OR title), which is right for konzibot re-pushes but far too loose applied
    queue-wide: two unrelated concerts in the same city on the same night share
    date+city and would each flag the other. A genuine duplicate has the same
    title (fuzzy — konzibot mangles them, so containment/ratio still lines up)
    or, when the title was rewritten entirely, the same venue *and* city.

    Used by scripts/scan_queue_duplicates.py to decide what to flag, and by the
    queue-duplicates page to separate real collisions from same-night context.
    """
    signals = set(match["signals"])
    return "title" in signals or {"venue", "city"} <= signals


def _match(kind, record_id, title, venue, city, date, time, signals):
    return {
        "kind": kind,
        "label": f"{title} @ {city or '?'}",
        "signals": [k for k, v in signals.items() if v],
        "id": record_id,
        "title": title,
        "venue": venue or "",
        "city": city or "",
        "date": date,
        "time": time,
    }


def find_konzibot_duplicates(
    start_date, city, venue_name, title, exclude_scraped_id=None
):
    """Return match dicts for calendar/queue events colliding with this one.

    A candidate collides when it shares the date and at least one of city /
    venue / title (normalized + fuzzy). Each match is
    {"kind", "label", "signals", "id", "title", "venue", "city", "date",
    "time"}; an empty list means no collision. ``kind`` says which table ``id``
    points at: "calendar" -> Event, "queue" -> ScrapedEvent.

    At ingest time the row being checked isn't in the session yet, so it can't
    self-match. When re-scanning rows that are already staged (the queue-dedup
    script, and the queue-duplicates page) pass their id as
    ``exclude_scraped_id`` to keep them from matching themselves.
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
            matches.append(
                _match(
                    "calendar",
                    event.id,
                    event.name,
                    venue.name if venue else "",
                    venue.city if venue and venue.city else "",
                    event.date,
                    event.doors,
                    signals,
                )
            )

    # Queue: other unapproved staged rows on the same date. The row being
    # ingested isn't in the session yet, so there's no self-match.
    queue_q = ScrapedEvent.query.filter(
        ScrapedEvent.status == ScrapedEvent.STATUS_PENDING,
        ScrapedEvent.start_date == start_date,
    )
    if exclude_scraped_id is not None:
        queue_q = queue_q.filter(ScrapedEvent.id != exclude_scraped_id)
    for sc in queue_q.all():
        signals = _match_signals(
            city, venue_name, title, sc.city or "", sc.venue_name or "", sc.title or ""
        )
        if any(signals.values()):
            matches.append(
                _match(
                    "queue",
                    sc.id,
                    sc.title or "(untitled)",
                    sc.venue_name or "",
                    sc.city or "",
                    sc.start_date,
                    sc.doors_open or sc.start_time,
                    signals,
                )
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
