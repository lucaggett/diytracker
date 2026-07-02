"""Add unique indexes on scraped_event.url and submitter.email.

Duplicate scraped rows (from the era of multiple concurrent schedulers) are
deduplicated first, keeping the oldest row per URL. Duplicate submitter emails
are not auto-deleted — the migration aborts and lists them for manual cleanup.
"""

import os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app, db

with app.app_context():
    with db.engine.connect() as conn:
        dupes = conn.execute(
            db.text(
                "SELECT email, COUNT(*) FROM submitter GROUP BY email HAVING COUNT(*) > 1"
            )
        ).fetchall()
        if dupes:
            for email, n in dupes:
                print(f"  duplicate submitter email: {email!r} ({n} rows)")
            sys.exit("Resolve duplicate submitter emails first, then re-run.")

        removed = conn.execute(
            db.text(
                "DELETE FROM scraped_event WHERE url IS NOT NULL AND id NOT IN "
                "(SELECT MIN(id) FROM scraped_event WHERE url IS NOT NULL GROUP BY url)"
            )
        ).rowcount
        if removed:
            print(f"Removed {removed} duplicate scraped_event rows")

        conn.execute(
            db.text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_scraped_event_url ON scraped_event (url)"
            )
        )
        conn.execute(
            db.text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_submitter_email ON submitter (email)"
            )
        )
        conn.commit()
print("Migration complete")
