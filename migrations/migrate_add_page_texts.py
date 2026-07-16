"""Add the page_text table: admin-editable per-locale intro paragraphs for
the canton and genre landing pages.

Fresh databases get the table via db.create_all() (which the app factory
already runs at boot, so this script is normally a no-op); it exists for the
manual-migration runbook and is safe to re-run.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from diytracker.app import create_app
from diytracker.models import PageText, db

app = create_app()

with app.app_context():
    existing = db.inspect(db.engine).get_table_names()
    if "page_text" in existing:
        print("  page_text already present, skipping")
    else:
        PageText.__table__.create(db.engine)
        print("  created page_text")
print("Migration complete")
