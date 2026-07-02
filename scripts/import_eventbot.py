"""One-shot importer for the Signal eventbot store.

Reads a local copy of the eventbot's source-agnostic store — an
`events.jsonl` plus its `flyers/` directory — and stages every record
(content AND flyer image) through services.ingest into the ScrapedEvent
queue. Dedup is by the eventbot's stable record id, so re-running after
a fresh sync only imports what's new.

Get a local copy of the store first, e.g.:

    rsync -a <eventbot-host>:path/to/eventbot/events/ instance/eventbot/

Then:

    uv run python scripts/import_eventbot.py                  # instance/eventbot/
    uv run python scripts/import_eventbot.py --store /path/to/copy

(For continuous production ingest use scripts/eventbot_forwarder.py on the
eventbot box instead — it POSTs to /api/ingest over HTTPS.)
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from diytracker.app import app
from diytracker.models import db
from diytracker.services.ingest import ingest_event


def eventbot_to_payload(rec):
    """Map one eventbot JSONL record onto the services.ingest payload."""
    ev = rec.get("event") or {}
    artists = [a.strip() for a in (ev.get("artists") or []) if a and a.strip()]
    description = (ev.get("description") or "").strip()
    age = (ev.get("age_restriction") or "").strip()
    if age:
        description = f"{description}\n\nAge restriction: {age}".strip()
    return {
        "source": "eventbot",
        "source_id": rec.get("id"),
        "submitter": rec.get("submitter"),
        "title": ev.get("title") or ", ".join(artists) or None,
        "performers": ", ".join(artists) or None,
        "styles": ev.get("genre"),
        "description": description or None,
        "start_date": ev.get("date"),
        "doors_open": ev.get("doors_time"),
        "start_time": ev.get("start_time"),
        "venue_name": ev.get("venue"),
        "street_address": ev.get("address"),
        "city": ev.get("city"),
        "ticket_price": ev.get("price"),
        "ticket_url": ev.get("ticket_url"),
    }


def load_flyer(rec, flyers_dir):
    """Return (bytes, filename) for the record's flyer, or None.

    The JSONL stores absolute paths from the eventbot box; only the
    basename is meaningful against our local copy.
    """
    flyer_path = rec.get("flyer_image")
    if not flyer_path:
        return None
    local = os.path.join(flyers_dir, os.path.basename(flyer_path))
    if not os.path.exists(local):
        return None
    with open(local, "rb") as f:
        return f.read(), os.path.basename(local)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--store",
        default=os.path.join("instance", "eventbot"),
        help="local copy of the eventbot events dir (default: instance/eventbot)",
    )
    args = ap.parse_args()

    jsonl = os.path.join(args.store, "events.jsonl")
    flyers_dir = os.path.join(args.store, "flyers")
    if not os.path.exists(jsonl):
        sys.exit(
            f"No events.jsonl in {args.store!r} — rsync the store first (see docstring)."
        )

    counts = {"created": 0, "duplicate": 0, "invalid": 0}
    missing_flyers = 0
    with app.app_context():
        with open(jsonl, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                flyer = load_flyer(rec, flyers_dir)
                if rec.get("flyer_image") and flyer is None:
                    missing_flyers += 1
                result = ingest_event(
                    eventbot_to_payload(rec), flyer=flyer, commit=False
                )
                counts[result.status] += 1
                if result.status == "invalid":
                    print(f"  skipped {rec.get('id')}: {result.reason}")
        db.session.commit()

    print(
        f"Imported {counts['created']} new events "
        f"({counts['duplicate']} already known, {counts['invalid']} invalid)."
    )
    if missing_flyers:
        print(
            f"Warning: {missing_flyers} records referenced flyers missing from {flyers_dir!r}."
        )


if __name__ == "__main__":
    main()
