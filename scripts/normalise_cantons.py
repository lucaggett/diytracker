"""One-off script: normalise canton values on existing database records.

Runs resolve_canton() over every Venue.canton and ScrapedEvent.region
in the database and writes back any values that change.

Usage::

    python scripts/normalise_cantons.py [--dry-run]

Pass --dry-run to print what would change without writing anything.
"""

import os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app, db
from models import Venue, ScrapedEvent
from utils import resolve_canton

dry_run = "--dry-run" in sys.argv

venue_updates = 0
scraped_updates = 0

with app.app_context():
    # --- Venues ---
    venues = Venue.query.all()
    for v in venues:
        normalised = resolve_canton(v.canton or "", v.city or "")
        if normalised != (v.canton or ""):
            print(
                f"Venue #{v.id} '{v.name}' ({v.city}): canton {v.canton!r} → {normalised!r}"
            )
            if not dry_run:
                v.canton = normalised
            venue_updates += 1

    # --- ScrapedEvent.region ---
    scraped = ScrapedEvent.query.all()
    for s in scraped:
        normalised = resolve_canton(s.region or "", s.city or "")
        if normalised != (s.region or ""):
            print(
                f"ScrapedEvent #{s.id} '{s.title}' ({s.city}): region {s.region!r} → {normalised!r}"
            )
            if not dry_run:
                s.region = normalised
            scraped_updates += 1

    if not dry_run:
        db.session.commit()
        print(
            f"\nDone. Updated {venue_updates} venue(s) and {scraped_updates} scraped event(s)."
        )
    else:
        print(
            f"\nDry run. Would update {venue_updates} venue(s) and {scraped_updates} scraped event(s)."
        )
