"""One-shot: add submitter.notify_label_events (July 2026).

Promoters get an email when an admin edits an event carrying one of their
labels; the flag is the opt-out. Schema comes from db.create_all(), which
can't ALTER an existing table, so add it by hand on the server once:

    uv run python scripts/migrate_add_notify_flag.py [path/to/events.db]

Idempotent — re-running is a no-op. Delete this file after the release is on
the server.
"""

import sqlite3
import sys

DB = sys.argv[1] if len(sys.argv) > 1 else "instance/events.db"


def main():
    conn = sqlite3.connect(DB)
    try:
        existing = {row[1] for row in conn.execute("PRAGMA table_info(submitter)")}
        if "notify_label_events" not in existing:
            conn.execute(
                "ALTER TABLE submitter "
                "ADD COLUMN notify_label_events BOOLEAN NOT NULL DEFAULT 1"
            )
            print("added submitter.notify_label_events")
        else:
            print("submitter.notify_label_events already present")
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    main()
