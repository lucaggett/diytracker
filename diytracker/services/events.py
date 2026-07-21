"""Event assembly shared by the submit, queue-approve and admin-edit routes."""

import hashlib

from diytracker.models import db, Event, ScrapedEvent, Venue
from diytracker.services.venue import get_or_create_venue
from diytracker.utils import clean_genre_tokens


def detach_scrape_approvals(event_id):
    """Null out scrape-queue approval links before deleting an event — the
    FK on scraped_event.approved_event_id would reject the delete otherwise.
    Does not commit."""
    return ScrapedEvent.query.filter_by(approved_event_id=event_id).update(
        {"approved_event_id": None}
    )


def compute_event_hash(
    name, date, doors, genre_str, acts, ticket_link, ticket_price, venue_id
):
    """Stable hash used to deduplicate events. All args should be strings or
    types with a consistent __str__ (datetime, time). genre_str must be a
    cleaned comma-joined string, not a raw list."""
    payload = (
        f"{name}{date}{doors}{genre_str}{acts}{ticket_link}{ticket_price}{venue_id}"
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def clean_genre_string(raw):
    """Normalise genres to the comma-joined string stored on Event.genre.

    Accepts a raw comma-separated string or a list of tokens (as the forms'
    multi-select produces); noise descriptors (concert, club, …) are dropped.
    """
    if isinstance(raw, (list, tuple)):
        raw = ", ".join(raw)
    return ", ".join(clean_genre_tokens(raw or ""))


def resolve_venue_from_form(form, require_new_venue_details=False):
    """Resolve the venue selection shared by the submit and admin-edit forms.

    `venue_id` is either a numeric id of an existing venue or "new"/empty for
    the inline venue fields. Returns (venue, created, error) where error is
    None on success, "not_found" for a bad id, or "missing_details" when
    `require_new_venue_details` is set and name/city/plz are incomplete.
    Does not commit (get_or_create_venue only flushes).
    """
    venue_id = form.venue_id.data
    if venue_id and venue_id != "new":
        venue = db.session.get(Venue, int(venue_id)) if venue_id.isdigit() else None
        if not venue:
            return None, False, "not_found"
        return venue, False, None
    if require_new_venue_details and not (
        form.venue_name.data and form.venue_city.data and form.venue_plz.data
    ):
        return None, False, "missing_details"
    venue, created = get_or_create_venue(
        name=form.venue_name.data,
        address=form.venue_address.data,
        city=form.venue_city.data,
        canton=form.venue_canton.data,
        plz=form.venue_plz.data,
        coords=form.venue_coords.data,
    )
    return venue, created, None


def create_event(
    *,
    name,
    date,
    doors,
    genre,
    acts,
    venue_id,
    ticket_link="",
    ticket_price="",
    end_date=None,
    is_festival=False,
    description=None,
    source_url=None,
    flyer=None,
    submitter_id=None,
    label_id=None,
    reject_duplicate=False,
):
    """Build an Event with its dedup hash and add it to the session.

    Does not commit — the caller owns the transaction (queue approval also
    marks the ScrapedEvent in the same commit). With `reject_duplicate`,
    returns None instead when an event with the same hash already exists.
    """
    event_hash = compute_event_hash(
        name, date, doors, genre, acts, ticket_link, ticket_price, venue_id
    )
    if reject_duplicate and Event.query.filter_by(event_hash=event_hash).first():
        return None
    event = Event(
        name=name,
        date=date,
        end_date=end_date,
        is_festival=is_festival,
        doors=doors,
        genre=genre,
        acts=acts,
        description=description,
        source_url=source_url,
        flyer=flyer,
        ticket_link=ticket_link,
        ticket_price=ticket_price,
        venue_id=venue_id,
        event_hash=event_hash,
        submitter_id=submitter_id,
        label_id=label_id,
    )
    db.session.add(event)
    return event
