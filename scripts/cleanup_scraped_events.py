"""One-off script: clean styles and ticket_url on existing ScrapedEvent rows.

Reuses the same ``clean_genre_tokens`` / ``clean_ticket_url`` helpers that the
import-time code now calls, so the DB matches what future scrapes produce.

Usage::

    python scripts/cleanup_scraped_events.py            # dry-run preview
    python scripts/cleanup_scraped_events.py --apply    # write changes

Preview-by-default is intentional — the script touches thousands of rows and
a dry run gives a line-by-line diff before anything is committed.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import or_

from app import app, db
from models import ScrapedEvent
from utils import clean_genre_tokens, clean_ticket_url

apply_changes = "--apply" in sys.argv

_WS_FIELDS = (
    "title",
    "performers",
    "venue_name",
    "city",
    "street_address",
    "organizer",
)

# Non-event-specific URLs that aren't the known generic placeholder. Reported
# for manual review but never auto-nulled — too diverse to pattern-match safely.
_EDGE_URL_MARKERS = ("/artist/", "rockthelakes.ch", "manoirpub.ch")
_EDGE_URL_EXACT = {
    "https://www.oeticket.com",
    "http://www.oeticket.com",
    "https://oeticket.com",
    "http://oeticket.com",
}


def _is_edge_url(url):
    if not url:
        return False
    if any(m in url for m in _EDGE_URL_MARKERS):
        return True
    normalised = url.split("?", 1)[0].split("#", 1)[0].rstrip("/").lower()
    return normalised in _EDGE_URL_EXACT


def main():
    styles_updates = 0
    ticket_nulled = 0
    ws_stripped = 0
    status_cleared = 0
    edge_cases = []

    with app.app_context():
        rows = ScrapedEvent.query.all()

        # --- 1. styles normalisation ---
        print("=== styles ===")
        for s in rows:
            new = ", ".join(clean_genre_tokens(s.styles or "")) or None
            if new != s.styles:
                print(f"#{s.id} '{s.title}': styles {s.styles!r} → {new!r}")
                if apply_changes:
                    s.styles = new
                styles_updates += 1

        # --- 2. generic metalgigs ticket_url → NULL ---
        print("\n=== ticket_url (metalgigs generics) ===")
        for s in rows:
            if s.source != "metalgigs" or not s.ticket_url:
                continue
            new = clean_ticket_url(s.ticket_url, s.source)
            if new != s.ticket_url:
                print(f"#{s.id} '{s.title}': ticket_url {s.ticket_url!r} → None")
                if apply_changes:
                    s.ticket_url = None
                ticket_nulled += 1

        # --- 3. edge-case URL report (no fix) ---
        # Check from the in-memory rows — after pass 2, generics are already
        # NULL in apply-mode so they correctly drop out here.
        for s in rows:
            if s.source == "metalgigs" and _is_edge_url(s.ticket_url):
                edge_cases.append((s.id, s.ticket_url))

        # --- 4. whitespace strip ---
        print("\n=== whitespace ===")
        for s in rows:
            for field in _WS_FIELDS:
                val = getattr(s, field)
                if isinstance(val, str) and val != val.strip():
                    stripped = val.strip()
                    print(f"#{s.id} {field}: {val!r} → {stripped!r}")
                    if apply_changes:
                        setattr(s, field, stripped)
                    ws_stripped += 1

        # --- 5. event_status '' → NULL ---
        print("\n=== event_status ('' → NULL) ===")
        blank_status = ScrapedEvent.query.filter(ScrapedEvent.event_status == "").all()
        status_cleared = len(blank_status)
        if blank_status:
            print(f"{status_cleared} rows with empty event_status will be set to NULL")
            if apply_changes:
                for s in blank_status:
                    s.event_status = None

        # --- 6. edge-case URL report ---
        if edge_cases:
            print("\nEDGE CASES — not modified, manual review recommended:")
            for eid, url in edge_cases:
                print(f"  #{eid}  {url}")

        # --- 7. report-only data-quality counts ---
        null_titles = ScrapedEvent.query.filter(ScrapedEvent.title.is_(None)).count()
        empty_venues = ScrapedEvent.query.filter(
            or_(ScrapedEvent.venue_name.is_(None), ScrapedEvent.venue_name == "")
        ).count()
        print("\nDATA-QUALITY REPORT (not auto-fixed):")
        print(f"  NULL titles:       {null_titles}")
        print(f"  Empty venue_name:  {empty_venues}")

        if apply_changes:
            db.session.commit()
            print(
                f"\nApplied. styles: {styles_updates}, ticket_url nulled: {ticket_nulled}, "
                f"whitespace stripped: {ws_stripped}, event_status cleared: {status_cleared}."
            )
        else:
            print(
                f"\nDry run. styles: {styles_updates}, ticket_url would null: {ticket_nulled}, "
                f"whitespace would strip: {ws_stripped}, event_status would clear: {status_cleared}."
            )
            print("Re-run with --apply to write.")


if __name__ == "__main__":
    main()
