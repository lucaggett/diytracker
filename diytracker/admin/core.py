"""Shared plumbing: app loading, schema pre-flight, small formatters."""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ACCESS_LOG = PROJECT_ROOT / "logs" / "access_log_diytracker"
ERROR_LOG = PROJECT_ROOT / "logs" / "error_log_diytracker"


class AdminError(Exception):
    """A user-facing failure (unknown user, schema drift, missing file).

    The CLI turns this into sys.exit(str(exc)); the TUI shows it as an
    error notification. Logic modules raise it instead of exiting.
    """


def load_app(db_path=None):
    """Build the Flask app via the factory (never starts the scraper thread)."""
    sys.path.insert(0, str(PROJECT_ROOT))
    from dotenv import load_dotenv

    from diytracker.app import create_app
    from diytracker.config import Config
    from diytracker.models import Submitter, db

    load_dotenv(PROJECT_ROOT / ".env")
    try:
        config = Config.from_env()
    except RuntimeError as exc:
        raise AdminError(str(exc)) from exc
    if db_path is not None:
        config.SQLALCHEMY_DATABASE_URI = f"sqlite:///{db_path}"
    return create_app(config), db, Submitter


def missing_columns(db, models):
    """Model columns absent from the live schema (DB behind the code)."""
    from sqlalchemy import inspect

    inspector = inspect(db.engine)
    missing = []
    for model in models:
        table = model.__tablename__
        actual = {column["name"] for column in inspector.get_columns(table)}
        missing.extend(
            f"{table}.{column.name}"
            for column in model.__table__.columns
            if column.name not in actual
        )
    return missing


def require_schema(db, models, hint):
    """Raise AdminError if the live schema is behind the given models."""
    missing = missing_columns(db, models)
    if missing:
        raise AdminError(
            "Database schema is behind the models; missing column(s): "
            + ", ".join(missing)
            + f"\n{hint}"
        )


def fmt_size(n_bytes):
    for unit in ("B", "KiB", "MiB", "GiB"):
        if n_bytes < 1024 or unit == "GiB":
            return f"{n_bytes:.1f} {unit}" if unit != "B" else f"{n_bytes} B"
        n_bytes /= 1024
