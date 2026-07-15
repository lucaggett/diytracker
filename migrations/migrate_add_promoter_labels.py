"""Add promoter/label columns: submitter.is_promoter, event.label_id.

The `label` table itself is created by db.create_all() when the app starts
(create_app runs it), so only the two new columns on existing tables need
ALTERs here. SQLite allows constant defaults in ALTER TABLE ADD COLUMN, so
is_promoter gets its NOT NULL DEFAULT 0 inline; label_id is nullable.

Safe to re-run: existing columns are skipped.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from diytracker.app import create_app
from diytracker.models import db

app = create_app()

COLUMNS = {
    "submitter": [
        ("is_promoter", "BOOLEAN NOT NULL DEFAULT 0"),
    ],
    "event": [
        ("label_id", "INTEGER REFERENCES label(id)"),
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
                print(f"  added {table}.{name}")
        conn.commit()
print("Migration complete")
