import os
import sys

# add the parent diytracker directory to the path (fixes weird import errors)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app, db

with app.app_context():
    with db.engine.connect() as conn:
        conn.execute(
            db.text("ALTER TABLE submitter ADD COLUMN password_hash VARCHAR(256)")
        )
        conn.execute(
            db.text(
                "ALTER TABLE submitter ADD COLUMN is_admin BOOLEAN NOT NULL DEFAULT 0"
            )
        )
        conn.commit()
print("Migration complete")
