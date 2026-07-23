"""One-shot fix, July 2026: move every event claimed under the DIYTracker
label over to Music & Résistance.

The promoter claim form used to preselect the alphabetically first label
for admins (who saw every label), so five events belonging to Music &
Résistance were silently claimed under DIYTracker. Run once on the server,
then delete:

    uv run python scripts/reassign_diytracker_label_events.py

Clears the page cache afterwards; no restart needed.
"""

import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = ROOT / "instance" / "cache"
DB_PATH = ROOT / "instance" / "events.db"
SOURCE_SLUG = "diytracker"
TARGET_SLUG = "music-resistance"


def main():
    conn = sqlite3.connect(DB_PATH)
    try:
        labels = {
            slug: label_id
            for label_id, slug in conn.execute(
                "SELECT id, slug FROM label WHERE slug IN (?, ?)",
                (SOURCE_SLUG, TARGET_SLUG),
            )
        }
        missing = {SOURCE_SLUG, TARGET_SLUG} - set(labels)
        if missing:
            sys.exit(f"label slug(s) not found: {', '.join(sorted(missing))}")

        rows = conn.execute(
            "SELECT id, name FROM event WHERE label_id = ?", (labels[SOURCE_SLUG],)
        ).fetchall()
        if not rows:
            print("Nothing to do: no events carry the DIYTracker label.")
            return
        for event_id, name in rows:
            print(f"  #{event_id}  {name}")
        conn.execute(
            "UPDATE event SET label_id = ? WHERE label_id = ?",
            (labels[TARGET_SLUG], labels[SOURCE_SLUG]),
        )
        conn.commit()
        print(f"Moved {len(rows)} event(s) to '{TARGET_SLUG}'.")
    finally:
        conn.close()

    if CACHE_DIR.exists():
        shutil.rmtree(CACHE_DIR)
        CACHE_DIR.mkdir()
        print("Page cache cleared.")


if __name__ == "__main__":
    main()
