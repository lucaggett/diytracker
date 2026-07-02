import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app, db

with app.app_context():
    with db.engine.connect() as conn:
        conn.execute(
            db.text("ALTER TABLE venue ADD COLUMN accessibility_token VARCHAR(64)")
        )
        conn.execute(
            db.text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_venue_accessibility_token ON venue (accessibility_token)"
            )
        )
        conn.execute(
            db.text("""
            CREATE TABLE IF NOT EXISTS venue_accessibility (
                id INTEGER PRIMARY KEY,
                venue_id INTEGER NOT NULL UNIQUE REFERENCES venue(id),
                updated_at DATETIME NOT NULL,
                step_free_entrance VARCHAR(20),
                step_free_entrance_notes TEXT,
                step_free_interior VARCHAR(20),
                accessible_toilet VARCHAR(20),
                accessible_toilet_notes TEXT,
                wheelchair_spaces VARCHAR(5),
                wheelchair_spaces_count INTEGER,
                floor_surface VARCHAR(50),
                strobe_lights VARCHAR(20),
                strobe_warning VARCHAR(20),
                smoke_machines VARCHAR(20),
                sound_level VARCHAR(20),
                quiet_space VARCHAR(5),
                earplugs_available VARCHAR(5),
                sensory_friendly_events VARCHAR(20),
                hearing_loop VARCHAR(20),
                sign_language VARCHAR(20),
                medication_fridge VARCHAR(20),
                first_aid_kit VARCHAR(5),
                aed_on_site VARCHAR(20),
                accessible_parking VARCHAR(20),
                public_transport_notes TEXT,
                gender_neutral_toilets VARCHAR(5),
                seating_areas VARCHAR(5),
                guide_dogs_welcome VARCHAR(5),
                quiet_entrance VARCHAR(5),
                additional_notes TEXT
            )
        """)
        )
        conn.commit()
print("Migration complete")
