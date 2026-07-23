"""One-shot: add scraped_event.needs_review + review_reason (July 2026).

The stricter konzibot duplicate check flags a staged event that collides
on date + city/venue/title with something already on the calendar or in the
queue. That needs two new columns on scraped_event. Schema comes from
db.create_all(), which can't ALTER an existing table, so add them by hand on
the server once:

    uv run python scripts/migrate_add_needs_review.py [path/to/events.db]

Idempotent — re-running is a no-op. Delete this file after the release is on
the server.
"""

import sqlite3
import sys

DB = sys.argv[1] if len(sys.argv) > 1 else "instance/events.db"


def _columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def main():
    conn = sqlite3.connect(DB)
    try:
        existing = _columns(conn, "scraped_event")
        if "needs_review" not in existing:
            conn.execute(
                "ALTER TABLE scraped_event "
                "ADD COLUMN needs_review BOOLEAN NOT NULL DEFAULT 0"
            )
            print("added scraped_event.needs_review")
        else:
            print("scraped_event.needs_review already present")
        if "review_reason" not in existing:
            conn.execute(
                "ALTER TABLE scraped_event ADD COLUMN review_reason VARCHAR(300)"
            )
            print("added scraped_event.review_reason")
        else:
            print("scraped_event.review_reason already present")
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    main()
