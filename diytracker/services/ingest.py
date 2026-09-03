"""Unified event ingest.

Every source that feeds the ScrapedEvent staging queue — the built-in
scrapers, the POST /api/ingest push endpoint, one-shot importers — goes
through ingest_event() so validation, normalisation, dedup and flyer
storage live in one place. Adding a source means producing the payload
below; nothing downstream needs to change.

Payload keys (all strings unless noted; only source, title and a valid
start_date are required):

    source        short source tag, e.g. 'petzi', 'eventbot'   (required)
    source_id     stable per-source record id; dedup key for sources
                  without a canonical URL
    url           canonical event URL; dedup key when present
    title         (required)
    performers, styles, description, venue_name, street_address, city,
    region, postal_code, ticket_price, ticket_currency, ticket_url,
    organizer, event_status, submitter
    start_date    'YYYY-MM-DD' or datetime.date                (required)
    end_date      'YYYY-MM-DD' or datetime.date
    doors_open    'HH:MM' or datetime.time
    start_time    'HH:MM' or datetime.time

A flyer image may accompany the payload, either as a werkzeug FileStorage
or as a (bytes, filename) tuple; it is validated/resized/stored via
services.uploads and the resulting path lands on ScrapedEvent.flyer.
"""

import io
from datetime import date as date_type
from datetime import datetime
from datetime import time as time_type
from typing import NamedTuple

from werkzeug.datastructures import FileStorage

from diytracker.models import Event, ScrapedEvent, db
from diytracker.services.genre_catalog import canonicalize_genre_string
from diytracker.services.ingest_dedup import (
    describe_duplicates,
    find_konzibot_duplicates,
)
from diytracker.services.uploads import UPLOAD_FOLDER, save_flyer_file
from diytracker.services.venue import canonical_venue_name
from diytracker.utils import clean_genre_tokens, clean_ticket_url, resolve_canton


class IngestResult(NamedTuple):
    status: str  # 'created' | 'duplicate' | 'invalid'
    reason: str
    record: object


# Sources that don't respect the API contract (canton/genre/venue spellings)
# and re-push shows already known: their payloads get genre/venue canonicalized
# and run through the aggressive same-day duplicate check in services.ingest_dedup,
# which flags collisions for a second manual look instead of dropping them.
STRICT_DEDUP_SOURCES = {"konzibot"}

# Column length caps (SQLite doesn't enforce VARCHAR sizes; trim on the way
# in so payloads from external pushers can't bloat rows).
_MAX_LEN = {
    "source": 20,
    "url": 300,
    "title": 200,
    "styles": 200,
    "venue_name": 200,
    "street_address": 200,
    "city": 100,
    "region": 100,
    "postal_code": 20,
    "ticket_price": 50,
    "ticket_currency": 10,
    "ticket_url": 300,
    "organizer": 200,
    "event_status": 100,
    "source_id": 64,
    "submitter": 200,
}


def parse_date(value):
    """Accept a date or 'YYYY-MM-DD' string; None/invalid -> None."""
    if isinstance(value, date_type):
        return value
    try:
        return datetime.strptime(str(value).strip(), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def parse_time(value):
    """Accept a time or 'HH:MM' string; None/invalid -> None."""
    if isinstance(value, time_type):
        return value
    try:
        return datetime.strptime(str(value).strip(), "%H:%M").time()
    except (TypeError, ValueError):
        return None


def _clean(payload, key):
    val = payload.get(key)
    if val is None:
        return None
    val = str(val).strip()
    if not val:
        return None
    cap = _MAX_LEN.get(key)
    return val[:cap] if cap else val


def _as_filestorage(flyer):
    if flyer is None or isinstance(flyer, FileStorage):
        return flyer
    data, filename = flyer
    return FileStorage(stream=io.BytesIO(data), filename=filename)


def ingest_event(payload, flyer=None, upload_folder=None, commit=True):
    """Validate, normalise and stage one event; returns an IngestResult.

    Dedup: `url` against ScrapedEvent.url and Event.source_url (an event
    already approved from that URL stays gone from the queue), then
    (source, source_id). With commit=False the row is added to the session
    but not committed — batch callers commit once at the end; in-batch
    duplicates are still caught because the dedup queries autoflush.
    """
    source = _clean(payload, "source")
    title = _clean(payload, "title")
    start_date = parse_date(payload.get("start_date"))
    if not source:
        return IngestResult("invalid", "missing source", None)
    if not title:
        return IngestResult("invalid", "missing title", None)
    if not start_date:
        return IngestResult(
            "invalid", "missing or malformed start_date (want YYYY-MM-DD)", None
        )

    url = _clean(payload, "url")
    if url and (
        ScrapedEvent.query.filter_by(url=url).first()
        or Event.query.filter_by(source_url=url).first()
    ):
        return IngestResult("duplicate", f"url already known: {url}", None)
    source_id = _clean(payload, "source_id")
    if (
        source_id
        and ScrapedEvent.query.filter_by(source=source, source_id=source_id).first()
    ):
        return IngestResult(
            "duplicate", f"source_id already known: {source}/{source_id}", None
        )

    flyer_path = None
    fs = _as_filestorage(flyer)
    if fs is not None:
        flyer_path = save_flyer_file(fs, upload_folder or UPLOAD_FOLDER)
        if flyer_path is None:
            return IngestResult(
                "invalid", "flyer rejected (not a png/jpg/gif image)", None
            )

    strict = (source or "").lower() in STRICT_DEDUP_SOURCES
    city = _clean(payload, "city")
    venue_name = _clean(payload, "venue_name")
    region = resolve_canton(_clean(payload, "region") or "", city or "")
    styles = (
        ", ".join(clean_genre_tokens(_clean(payload, "styles") or ""))[
            : _MAX_LEN["styles"]
        ]
        or None
    )
    if strict:
        # konzibot ignores the catalog/venue spellings, so snap them onto the
        # names already in the DB before staging.
        venue_name = canonical_venue_name(venue_name, city) if venue_name else None
        canon = canonicalize_genre_string(_clean(payload, "styles") or "")
        styles = canon[: _MAX_LEN["styles"]] or None

    record = ScrapedEvent(
        source=source,
        source_id=source_id,
        url=url,
        title=title,
        performers=_clean(payload, "performers"),
        styles=styles,
        description=_clean(payload, "description"),
        start_date=start_date,
        end_date=parse_date(payload.get("end_date")),
        doors_open=parse_time(payload.get("doors_open")),
        start_time=parse_time(payload.get("start_time")),
        venue_name=venue_name,
        street_address=_clean(payload, "street_address"),
        city=city,
        region=region,
        postal_code=_clean(payload, "postal_code"),
        ticket_price=_clean(payload, "ticket_price"),
        ticket_currency=_clean(payload, "ticket_currency"),
        ticket_url=clean_ticket_url(_clean(payload, "ticket_url"), source),
        organizer=_clean(payload, "organizer"),
        event_status=_clean(payload, "event_status"),
        submitter=_clean(payload, "submitter"),
        flyer=flyer_path,
    )
    if strict:
        # Same-day collision with the calendar or the queue -> keep it, but
        # flag it for a second manual look instead of silently duplicating.
        matches = find_konzibot_duplicates(start_date, city, venue_name, title)
        if matches:
            record.needs_review = True
            record.review_reason = describe_duplicates(matches)
    db.session.add(record)
    if commit:
        db.session.commit()
    return IngestResult("created", None, record)
