"""One-shot: add label profile columns (July 2026).

Labels gain public profile fields — website, two social links, a contact
email. Schema comes from db.create_all(), which can't ALTER an existing
table, so add them by hand on the server once:

    uv run python scripts/migrate_add_label_profile.py [path/to/events.db]

Idempotent — re-running is a no-op. Delete this file after the release is on
the server.
"""

import sqlite3
import sys

DB = sys.argv[1] if len(sys.argv) > 1 else "instance/events.db"

COLUMNS = {
    "website": "VARCHAR(300)",
    "link_social_1": "VARCHAR(300)",
    "link_social_2": "VARCHAR(300)",
    "contact_email": "VARCHAR(200)",
}


def _columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def main():
    conn = sqlite3.connect(DB)
    try:
        existing = _columns(conn, "label")
        for name, sql_type in COLUMNS.items():
            if name in existing:
                print(f"label.{name} already present")
                continue
            conn.execute(f"ALTER TABLE label ADD COLUMN {name} {sql_type}")
            print(f"added label.{name}")
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    main()
