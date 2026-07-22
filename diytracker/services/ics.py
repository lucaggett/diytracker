"""RFC 5545 (iCalendar) serialisation for events.

Written by hand rather than pulled from a library: the output is one
VCALENDAR with a VEVENT per event, and the only fiddly parts — escaping,
75-octet line folding, and a VTIMEZONE block — are a few dozen lines each.

Start and end times are derived exactly like `event_json_ld()` in
services/seo.py (doors in Europe/Zurich, end_date falling back to the start
day), so a subscribed calendar and the structured data on the page can never
disagree about when a show is.

Times are emitted as TZID=Europe/Zurich against an embedded VTIMEZONE rather
than as floating local times, which would land at the wrong hour for anyone
whose device is not on Swiss time.
"""

from datetime import datetime, timedelta, timezone

from flask import url_for

from diytracker.services.seo import (
    ZURICH,
    canonical_url,
    parse_acts,
    parse_swiss_coords,
)
from diytracker.utils import is_safe_link, parent_genres

PRODID = "-//diytracker.ch//diytracker//DE"
TZID = "Europe/Zurich"
UID_DOMAIN = "diytracker.ch"

# Events with no end_date get a nominal length; nothing in the data says when
# a show finishes, and a zero-length entry renders as a bare marker in most
# calendar apps.
DEFAULT_DURATION = timedelta(hours=4)

# Static VTIMEZONE for Europe/Zurich: CET/CEST, EU switchover rules (last
# Sunday of March / October). Stable since 1996 and not worth generating.
_VTIMEZONE = [
    "BEGIN:VTIMEZONE",
    f"TZID:{TZID}",
    "BEGIN:DAYLIGHT",
    "TZOFFSETFROM:+0100",
    "TZOFFSETTO:+0200",
    "TZNAME:CEST",
    "DTSTART:19700329T020000",
    "RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=-1SU",
    "END:DAYLIGHT",
    "BEGIN:STANDARD",
    "TZOFFSETFROM:+0200",
    "TZOFFSETTO:+0100",
    "TZNAME:CET",
    "DTSTART:19701025T030000",
    "RRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU",
    "END:STANDARD",
    "END:VTIMEZONE",
]

_STATUS = {"cancelled": "CANCELLED", "postponed": "TENTATIVE"}


def escape(value):
    """Escape a text value per RFC 5545 §3.3.11."""
    return (
        str(value or "")
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
        .replace("\r", "\\n")
    )


def fold(line):
    """Fold a content line to 75 octets, continuations prefixed with a space.

    Folding is measured in octets, not characters, and a multi-byte character
    must not be split across the fold — venue names like "Zürich" would
    otherwise emit invalid UTF-8.
    """
    encoded = line.encode("utf-8")
    if len(encoded) <= 75:
        return line
    chunks = []
    start = 0
    limit = 75
    while start < len(encoded):
        end = min(start + limit, len(encoded))
        # Back off to a character boundary (continuation bytes are 10xxxxxx).
        while end > start and end < len(encoded) and encoded[end] & 0xC0 == 0x80:
            end -= 1
        chunks.append(encoded[start:end].decode("utf-8"))
        start = end
        # Continuation lines carry a leading space, so they fit one octet less.
        limit = 74
    return "\r\n ".join(chunks)


def _utc_stamp(value):
    if value is None:
        value = datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _local_stamp(value):
    return value.strftime("%Y%m%dT%H%M%S")


def event_bounds(event):
    """(start, end) as timezone-aware Europe/Zurich datetimes.

    Mirrors event_json_ld: doors on the event date is the start; a festival's
    end_date covers the whole final day, so the exclusive DTEND is midnight of
    the day after.
    """
    start = datetime.combine(event.date.date(), event.doors, tzinfo=ZURICH)
    if event.end_date and event.end_date > event.date.date():
        end = datetime.combine(
            event.end_date + timedelta(days=1), datetime.min.time(), tzinfo=ZURICH
        )
    else:
        end = start + DEFAULT_DURATION
    return start, end


def _location(venue):
    parts = [venue.name]
    if venue.address:
        parts.append(venue.address)
    town = " ".join(part for part in (venue.plz, venue.city) if part)
    if town:
        parts.append(town)
    return ", ".join(parts)


def _description(event):
    parts = []
    acts = parse_acts(event.acts)
    if acts:
        parts.append(", ".join(acts))
    if event.genre:
        parts.append(event.genre)
    if event.ticket_price:
        parts.append(event.ticket_price)
    if event.description:
        parts.append(" ".join(event.description.split()))
    # Same guard as the templates: a non-http(s) ticket link is never rendered.
    if is_safe_link(event.ticket_link):
        parts.append(event.ticket_link)
    parts.append(canonical_url(url_for("public.event_page", event_id=event.id)))
    return "\n".join(parts)


def event_lines(event):
    """The VEVENT block for one event, as unfolded content lines."""
    start, end = event_bounds(event)
    lines = [
        "BEGIN:VEVENT",
        f"UID:event-{event.id}@{UID_DOMAIN}",
        f"DTSTAMP:{_utc_stamp(event.updated_at)}",
        f"DTSTART;TZID={TZID}:{_local_stamp(start)}",
        f"DTEND;TZID={TZID}:{_local_stamp(end)}",
        f"SUMMARY:{escape(event.name or event.acts or 'Event')}",
        f"LOCATION:{escape(_location(event.venue))}",
        f"DESCRIPTION:{escape(_description(event))}",
        f"URL:{canonical_url(url_for('public.event_page', event_id=event.id))}",
    ]
    # SEQUENCE must grow when a listing is edited, or subscribed clients keep
    # the version they already have.
    if event.updated_at:
        lines.append(f"SEQUENCE:{int(event.updated_at.timestamp())}")
    coords = parse_swiss_coords(event.venue.coords)
    if coords:
        lines.append(f"GEO:{coords[0]};{coords[1]}")
    genres = parent_genres(event.genre)
    if genres:
        lines.append(f"CATEGORIES:{','.join(escape(g) for g in genres)}")
    status = _STATUS.get(event.status)
    if status:
        lines.append(f"STATUS:{status}")
    lines.append("END:VEVENT")
    return lines


def build_calendar(events, name, description=None):
    """Serialise *events* as a complete VCALENDAR document."""
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{PRODID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"NAME:{escape(name)}",
        f"X-WR-CALNAME:{escape(name)}",
        # Six hours: the scraper runs far less often than that, and a
        # subscription that re-polls harder buys nothing.
        "REFRESH-INTERVAL;VALUE=DURATION:PT6H",
        "X-PUBLISHED-TTL:PT6H",
        f"X-WR-TIMEZONE:{TZID}",
    ]
    if description:
        lines.append(f"X-WR-CALDESC:{escape(description)}")
    lines += _VTIMEZONE
    for event in events:
        lines += event_lines(event)
    lines.append("END:VCALENDAR")
    return "\r\n".join(fold(line) for line in lines) + "\r\n"
