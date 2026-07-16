"""Add label.description, shown on the public label page's hero header.

Fresh databases get the column via db.create_all(); existing databases need
this run once, manually, on the server.

Safe to re-run: existing columns are skipped.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from diytracker.app import create_app
from diytracker.models import db

app = create_app()

COLUMNS = {
    "label": [
        ("description", "TEXT"),
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
