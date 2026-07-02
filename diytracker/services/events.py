import hashlib


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
