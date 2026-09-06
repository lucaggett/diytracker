"""Aggressive duplicate detection for everything entering the queue.

The unified ingest core dedupes by canonical URL or (source, source_id). That
misses the case every source keeps hitting: the *same show* arriving with a
fresh id or a second URL, so it lands as a new queue entry alongside the copy
already on the calendar or in the queue. This module flags those collisions on
the way in.

Matching is deliberately loose: sources mangle cantons, venue spellings and
titles, so we normalize hard (fold diacritics, strip PLZ/canton, collapse
whitespace — reusing services.venue) and fuzzy-compare. Same date is the
anchor; a single further signal (city, venue or title) is enough to notice.

Noticing and hiding are two different things, and the flag carries both:

- **strong** (``is_strong_match``: same title, or same venue *and* city) — a
  real duplicate. ``needs_review`` is set, the row drops out of the web queue
  and is triaged on /queue/duplicates.
- **weak** (the same night in the same town, nothing more) — usually two
  unrelated bands. Only ``review_reason`` is written; the row stays in the
  normal queue behind an inline warning, because burying every same-night pair
  would move most of the queue onto the duplicates page.

Each match carries the matched record's id and its date/venue/city/title, so
the queue-duplicates page can link straight to the colliding show and put the
two side by side.

None of that helps against a copy whose title, venue *and* city were all
rewritten by the source — it matches on no signal at all. ``find_same_date_events``
is the fallback for that: not a matcher, just the day's full calendar lineup,
which the approval page makes the admin read before publishing.
"""

from datetime import datetime, timedelta
from datetime import time as time_type
from difflib import SequenceMatcher

from diytracker.models import Event, ScrapedEvent
from diytracker.services.venue import normalize_city, normalize_name

_FUZZY_THRESHOLD = 0.82
_MIN_CONTAINMENT = 4
_REASON_CAP = 300


def _similar(a, b):
    """True when two normalized strings are the same, one contains the other
    (both non-trivial), or they're fuzzily close.
    """
    if not a or not b:
        return False
    if a == b:
        return True
    if len(a) >= _MIN_CONTAINMENT and len(b) >= _MIN_CONTAINMENT and (a in b or b in a):
        return True
    return SequenceMatcher(None, a, b).ratio() >= _FUZZY_THRESHOLD


def name_similarity(a, b):
    """Public fuzzy name compare for other callers (label claim suggestions):
    same normalization + matching rules as the konzibot dedup.
    """
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

    ``find_duplicate_matches`` reports on a *single* signal (same date + city OR
    venue OR title), which is what you want for noticing a re-push, but far too
    loose to act on: two unrelated concerts in the same city on the same night
    share date+city and would each flag the other. A genuine duplicate has the
    same title (fuzzy — sources mangle them, so containment/ratio still lines
    up) or, when the title was rewritten entirely, the same venue *and* city.

    This is the line between the two levels: strong matches set needs_review at
    ingest and block an approval, weak ones only warn. Also used by
    scripts/scan_queue_duplicates.py and by the queue-duplicates page to
    separate real collisions from same-night context.
    """
    signals = set(match["signals"])
    return "title" in signals or {"venue", "city"} <= signals


def has_strong_match(matches):
    """True when any of these collisions is a real duplicate, not just a
    same-night neighbour. Decides needs_review at ingest time.
    """
    return any(is_strong_match(m) for m in matches)


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


def find_duplicate_matches(
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


def find_same_date_events(start_date, exclude_event_ids=()):
    """Every calendar event on ``start_date``, earliest first.

    ``find_duplicate_matches`` only reports collisions it recognises — same
    city, venue or title. A show that is already listed under a rewritten
    title, at a venue spelled differently, with the city left blank matches on
    none of the three and sails through the approval gate. So the last thing
    the approval page shows is the whole night, unfiltered, for the admin to
    read: the machine cannot rule the copy out, a human looking at five shows
    can. ``exclude_event_ids`` drops the ones the duplicate section above it is
    already showing.

    Each row is {"id", "title", "venue", "city", "time"} — the date is the
    heading, so it isn't repeated per row.
    """
    if start_date is None:
        return []
    excluded = set(exclude_event_ids)
    day_start = datetime.combine(start_date, time_type.min)
    day_end = day_start + timedelta(days=1)
    rows = (
        Event.query.filter(Event.date >= day_start, Event.date < day_end)
        .order_by(Event.date.asc(), Event.doors.asc())
        .all()
    )
    return [
        {
            "id": e.id,
            "title": e.name,
            "venue": e.venue.name if e.venue else "",
            "city": (e.venue.city if e.venue else "") or "",
            "time": f"{e.doors:%H:%M}" if e.doors else "",
        }
        for e in rows
        if e.id not in excluded
    ]
