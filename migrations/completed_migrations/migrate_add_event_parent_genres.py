import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app, db
from models import Event, _format_parent_genres

with app.app_context():
    with db.engine.connect() as conn:
        try:
            conn.execute(
                db.text("ALTER TABLE event ADD COLUMN parent_genres VARCHAR(200)")
            )
        except Exception as exc:
            if "duplicate column" not in str(exc).lower():
                raise
        conn.execute(
            db.text(
                "CREATE INDEX IF NOT EXISTS ix_event_parent_genres ON event (parent_genres)"
            )
        )
        conn.commit()

    for e in Event.query.all():
        e.parent_genres = _format_parent_genres(e.genre)
    db.session.commit()
print("Migration complete")
