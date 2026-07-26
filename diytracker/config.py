"""Application configuration.

Single place where environment variables become Flask config. `create_app()`
in diytracker/app.py consumes a `Config` instance via `app.config.from_object`;
entrypoints that need overrides (tests, the TUI's alternate DB path)
build the instance themselves and mutate it before passing it in.

Environment variables read by `Config.from_env()` are documented in
`.env.example` at the repo root.
"""

import os
from datetime import timedelta

from diytracker.services.i18n import DEFAULT_LOCALE, SUPPORTED_LOCALES
from diytracker.services.uploads import ALLOWED_EXTENSIONS, UPLOAD_FOLDER
from diytracker.paths import TRANSLATIONS_DIR


class Config:
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    MAX_CONTENT_LENGTH = 5 * 1024 * 1024
    PERMANENT_SESSION_LIFETIME = timedelta(days=7)
    # Secure works over http://localhost in modern browsers, so dev logins are fine.
    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_SAMESITE = "Lax"
    UPLOAD_FOLDER = UPLOAD_FOLDER
    ALLOWED_EXTENSIONS = ALLOWED_EXTENSIONS
    SEND_FILE_MAX_AGE_DEFAULT = 31536000
    COMPRESS_ALGORITHM = ["br", "gzip"]
    BABEL_DEFAULT_LOCALE = DEFAULT_LOCALE
    BABEL_SUPPORTED_LOCALES = list(SUPPORTED_LOCALES)
    BABEL_TRANSLATION_DIRECTORIES = str(TRANSLATIONS_DIR)

    # Filled in by from_env() (or by a subclass / caller override).
    SQLALCHEMY_DATABASE_URI = "sqlite:///events.db"
    SECRET_KEY: str
    # Shared token for POST /api/ingest (external event sources). Unset =
    # endpoint disabled.
    INGEST_TOKEN = None
    # Canonical origin for <link rel="canonical">, og:url and JSON-LD URLs
    # (e.g. "https://diytracker.ch"). Empty = fall back to the request host
    # (dev/tests); production must set it so canonicals don't depend on headers.
    CANONICAL_HOST = ""

    @classmethod
    def from_env(cls):
        cfg = cls()
        secret_key = os.environ.get("SECRET_KEY")
        if not secret_key:
            raise RuntimeError(
                "SECRET_KEY is not set. Copy .env.example to .env and fill it in."
            )
        cfg.SECRET_KEY = secret_key
        cfg.SQLALCHEMY_DATABASE_URI = os.environ.get(
            "DATABASE_URI", cls.SQLALCHEMY_DATABASE_URI
        )
        cfg.INGEST_TOKEN = os.environ.get("INGEST_TOKEN")
        cfg.CANONICAL_HOST = os.environ.get("CANONICAL_HOST", "")
        return cfg


class TestConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    # The test client speaks plain http; Secure cookies would never be sent.
    SESSION_COOKIE_SECURE = False
    # Rapid-fire test requests look exactly like scraping; keep the detector
    # off except where a test enables it explicitly.
    SCRAPE_DETECTION_ENABLED = False
    SECRET_KEY = "test-secret-key"

    def __init__(self, database_uri):
        self.SQLALCHEMY_DATABASE_URI = database_uri
