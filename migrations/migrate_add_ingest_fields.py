"""Add multi-source ingest columns to scraped_event.

Adds `flyer` (path to a stored flyer image), `source_id` (stable per-source
record id for sources that have no canonical URL, e.g. the Signal eventbot)
and `submitter` (who posted it at the source), plus a unique index on
(source, source_id) so pushes from external sources dedup cleanly.

Safe to re-run: existing columns/indexes are skipped.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from diytracker.app import app, db

COLUMNS = [
    ("flyer", "VARCHAR(200)"),
    ("source_id", "VARCHAR(64)"),
    ("submitter", "VARCHAR(200)"),
]

with app.app_context():
    with db.engine.connect() as conn:
        existing = {
            row[1] for row in conn.execute(db.text("PRAGMA table_info(scraped_event)"))
        }
        for name, ddl_type in COLUMNS:
            if name in existing:
                print(f"  column {name} already present, skipping")
                continue
            conn.execute(
                db.text(f"ALTER TABLE scraped_event ADD COLUMN {name} {ddl_type}")
            )
            print(f"  added column {name}")

        conn.execute(
            db.text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_scraped_event_source_source_id "
                "ON scraped_event (source, source_id)"
            )
        )
        conn.commit()
print("Migration complete")
