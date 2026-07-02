import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app, db

with app.app_context():
    with db.engine.connect() as conn:
        conn.execute(
            db.text("CREATE INDEX IF NOT EXISTS ix_event_date ON event (date)")
        )
        conn.execute(
            db.text("CREATE INDEX IF NOT EXISTS ix_event_venue_id ON event (venue_id)")
        )
        conn.commit()
print("Migration complete")
