"""Add SEO columns: event.status, event.updated_at, venue.updated_at.

`status` (scheduled/cancelled/postponed) feeds schema.org eventStatus in the
event JSON-LD; `updated_at` feeds <lastmod> in the sitemap. SQLite forbids
non-constant defaults in ALTER TABLE ADD COLUMN, so updated_at is added
without a default and backfilled; the model default covers new rows.

Safe to re-run: existing columns are skipped.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from diytracker.app import app, db

COLUMNS = {
    "event": [
        ("status", "VARCHAR(20) NOT NULL DEFAULT 'scheduled'"),
        ("updated_at", "DATETIME"),
    ],
    "venue": [
        ("updated_at", "DATETIME"),
    ],
}

with app.app_context():
    with db.engine.connect() as conn:
        for table, columns in COLUMNS.items():
            existing = {
                row[1] for row in conn.execute(db.text(f"PRAGMA table_info({table})"))
            }
            for name, ddl in columns:
                if name in existing:
                    print(f"  {table}.{name} already present, skipping")
                    continue
                conn.execute(db.text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
                if name == "updated_at":
                    conn.execute(
                        db.text(
                            f"UPDATE {table} SET updated_at = datetime('now') "
                            "WHERE updated_at IS NULL"
                        )
                    )
                print(f"  added {table}.{name}")
        conn.commit()
print("Migration complete")
