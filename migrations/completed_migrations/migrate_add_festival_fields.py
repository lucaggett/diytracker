import os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app, db

with app.app_context():
    with db.engine.connect() as conn:
        conn.execute(db.text("ALTER TABLE event ADD COLUMN end_date DATE"))
        conn.execute(
            db.text(
                "ALTER TABLE event ADD COLUMN is_festival BOOLEAN NOT NULL DEFAULT 0"
            )
        )
        conn.commit()
print("Migration complete")
