"""One-shot repair for FK orphans (July 2026, prod incident /events/283/).

Venues 58-63 were deleted while events still pointed at them (SQLite FK
enforcement was off; enabled in this release). Two of the six were later
recreated by the scraper under new ids; this script repoints those events,
recreates the other four venues under their ORIGINAL ids (so their events
resolve without repointing), and nulls dangling scraped_event approval
links. Venue identities come from the scraped_event rows of the affected
events, so future scrapes match on (name, city, plz).

Idempotent — safe to re-run on a fresh prod copy. Run from the repo root:

    uv run python repair_orphaned_venues.py [path/to/events.db]

Delete this file once the repaired DB is back on the server.
"""

import sqlite3
import sys
from datetime import datetime, timezone

DB = sys.argv[1] if len(sys.argv) > 1 else "instance/events.db"

# Deleted venue id -> identity (from scraped_event venue data).
# survivor_match: (name, city) of an already-recreated venue to repoint to,
# instead of recreating the row.
MISSING_VENUES = {
    58: {
        "name": "Musigburg",
        "address": "Bahnhofstrasse 50",
        "city": "Aarburg",
        "canton": "AG",
        "plz": "4663",
    },
    59: {"survivor_match": ("Kuppel Basel", "Basel")},
    60: {
        "name": "Soho Kosmos",
        "address": "Wangenstrasse 45",
        "city": "Wiedlisbach / Wangen a.A.",
        "canton": "BE",
        "plz": "4537",
    },
    61: {
        "name": "Mahogany Hall",
        "address": "Klösterlistutz 18",
        "city": "Bern",
        "canton": "BE",
        "plz": "3013",
    },
    62: {"survivor_match": ("Götter Brauerei", "Baar")},
    63: {
        "name": "Camäleon",
        "address": "Fabrikweg 3",
        "city": "Vaduz",
        "canton": "LI",
        "plz": "9490",
    },
}


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(sep=" ")


def main():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    # FKs stay OFF for the repair itself (we're fixing violations).

    orphans = conn.execute(
        "SELECT e.id, e.venue_id, e.name FROM event e"
        " LEFT JOIN venue v ON v.id = e.venue_id WHERE v.id IS NULL"
    ).fetchall()
    print(f"{len(orphans)} orphaned events: {[r['id'] for r in orphans]}")

    unknown = {r["venue_id"] for r in orphans} - set(MISSING_VENUES)
    if unknown:
        sys.exit(
            f"Orphans point at unexpected venue ids {sorted(unknown)} — "
            "extend MISSING_VENUES before rerunning."
        )

    now = utcnow()
    for venue_id, spec in MISSING_VENUES.items():
        n_events = conn.execute(
            "SELECT count(*) FROM event WHERE venue_id = ?", (venue_id,)
        ).fetchone()[0]

        if "survivor_match" in spec:
            name, city = spec["survivor_match"]
            row = conn.execute(
                "SELECT id FROM venue WHERE name = ? AND city = ?", (name, city)
            ).fetchone()
            if row is None:
                sys.exit(
                    f"No surviving venue named {name!r} in {city!r} — "
                    "expected the scraper's recreated row."
                )
            if n_events:
                conn.execute(
                    "UPDATE event SET venue_id = ?, updated_at = ? WHERE venue_id = ?",
                    (row["id"], now, venue_id),
                )
                print(
                    f"repointed {n_events} event(s): venue {venue_id}"
                    f" -> #{row['id']} {name}"
                )
        else:
            exists = conn.execute(
                "SELECT 1 FROM venue WHERE id = ?", (venue_id,)
            ).fetchone()
            if exists:
                print(f"venue {venue_id} already exists — skipping")
                continue
            conn.execute(
                "INSERT INTO venue (id, name, address, city, canton, plz,"
                " coords, updated_at) VALUES (?, ?, ?, ?, ?, ?, '', ?)",
                (
                    venue_id,
                    spec["name"],
                    spec["address"],
                    spec["city"],
                    spec["canton"],
                    spec["plz"],
                    now,
                ),
            )
            print(
                f"recreated venue {venue_id} {spec['name']}"
                f" ({n_events} event(s) reattached)"
            )

    n = conn.execute(
        "UPDATE scraped_event SET approved_event_id = NULL"
        " WHERE approved_event_id IS NOT NULL"
        " AND approved_event_id NOT IN (SELECT id FROM event)"
    ).rowcount
    print(f"nulled {n} dangling scraped_event approval link(s)")

    conn.commit()

    remaining = conn.execute("PRAGMA foreign_key_check").fetchall()
    if remaining:
        sys.exit(
            f"FAILED: {len(remaining)} FK violations remain: "
            f"{[tuple(r) for r in remaining[:10]]}"
        )
    print("foreign_key_check clean ✓")
    conn.close()


if __name__ == "__main__":
    main()
