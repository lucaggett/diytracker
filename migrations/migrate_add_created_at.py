"""Add event.created_at for the contributions leaderboard.

`updated_at` resets on every edit, so it can't tell when an event was first
contributed. New rows get created_at from the model default; existing rows
are backfilled from updated_at (the best approximation available).

Safe to re-run: the column is skipped if present and the backfill only
touches NULL rows.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from diytracker.app import create_app
from diytracker.models import db

app = create_app()

with app.app_context():
    with db.engine.connect() as conn:
        existing = {row[1] for row in conn.execute(db.text("PRAGMA table_info(event)"))}
        if "created_at" in existing:
            print("  column created_at already present, skipping")
        else:
            conn.execute(db.text("ALTER TABLE event ADD COLUMN created_at DATETIME"))
            print("  added column created_at")
        result = conn.execute(
            db.text("UPDATE event SET created_at = updated_at WHERE created_at IS NULL")
        )
        conn.commit()
        print(f"  backfilled {result.rowcount} row(s) from updated_at")
print("Migration complete")
