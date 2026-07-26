"""One-shot: add scraped_event.status + reject_reason and backfill (July 2026).

The queue previously conflated rejection and publication in the `approved`
boolean (approved with no approved_event_id meant "removed from queue").
`status` makes the outcome explicit: pending / published / rejected, with an
optional reject_reason. Schema comes from db.create_all(), which can't ALTER
an existing table, so add and backfill by hand on the server once:

    uv run python scripts/migrate_add_queue_status.py [path/to/events.db]

Backfill: approved rows with an approved_event_id become 'published',
approved rows without one become 'rejected' (reason stays NULL — historic
rejections never had one), everything else stays 'pending'.

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
        backfill = "status" not in existing
        if backfill:
            conn.execute(
                "ALTER TABLE scraped_event "
                "ADD COLUMN status VARCHAR(20) NOT NULL DEFAULT 'pending'"
            )
            print("added scraped_event.status")
        else:
            print("scraped_event.status already present")
        if "reject_reason" not in existing:
            conn.execute(
                "ALTER TABLE scraped_event ADD COLUMN reject_reason VARCHAR(300)"
            )
            print("added scraped_event.reject_reason")
        else:
            print("scraped_event.reject_reason already present")

        if backfill:
            published = conn.execute(
                "UPDATE scraped_event SET status = 'published' "
                "WHERE approved = 1 AND approved_event_id IS NOT NULL"
            ).rowcount
            rejected = conn.execute(
                "UPDATE scraped_event SET status = 'rejected' "
                "WHERE approved = 1 AND approved_event_id IS NULL"
            ).rowcount
            print(f"backfilled: {published} published, {rejected} rejected")
        else:
            print("backfill skipped (status column pre-existed)")

        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_scraped_event_status "
            "ON scraped_event (status)"
        )
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    main()
