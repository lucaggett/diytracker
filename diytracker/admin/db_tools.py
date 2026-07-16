"""Database maintenance: WAL-safe backup and vacuum."""

import sqlite3
from datetime import datetime
from pathlib import Path

from diytracker.admin.core import PROJECT_ROOT, AdminError
from diytracker.models import db


def backup(dest=None):
    """Snapshot the database; returns (dest_path, size_bytes)."""
    src = Path(db.engine.url.database)
    if not src.is_file():
        raise AdminError(f"No database file at {src}")
    if dest:
        dest = Path(dest).resolve()
    else:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = PROJECT_ROOT / "backups" / f"diytracker-{stamp}.sqlite3"
    dest.parent.mkdir(parents=True, exist_ok=True)
    # sqlite3's online backup API copies a consistent snapshot even while
    # the WAL is live, unlike a plain file copy.
    source_conn = sqlite3.connect(src)
    dest_conn = sqlite3.connect(dest)
    try:
        source_conn.backup(dest_conn)
    finally:
        dest_conn.close()
        source_conn.close()
    return dest, dest.stat().st_size


def vacuum():
    """Compact the database file; returns (name, before_bytes, after_bytes)."""
    path = Path(db.engine.url.database)
    if not path.is_file():
        raise AdminError(f"No database file at {path}")
    before = path.stat().st_size
    # VACUUM refuses to run inside a transaction, hence autocommit; the
    # checkpoint folds the WAL back in so the reported size is real.
    with db.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(db.text("VACUUM"))
        conn.execute(db.text("PRAGMA wal_checkpoint(TRUNCATE)"))
    return path.name, before, path.stat().st_size
