"""Shared pytest fixtures.

The application (`app.py`) is a module-level singleton: importing it binds the
DB engine and starts the scraper thread. We make it test-friendly by:

  * setting SECRET_KEY + DATABASE_URI (a throwaway temp file) before import,
  * stubbing out the scraper scheduler so importing the app never touches the
    network or spawns a background thread,
  * disabling CSRF and turning on TESTING.

Schema is rebuilt per-test (drop_all/create_all) so tests are isolated.
"""

import os
import sys
import tempfile

import pytest

# --- Configure the environment *before* importing the app singleton. --------
_DB_FD, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="diytracker-test-")
os.close(_DB_FD)
os.environ["SECRET_KEY"] = "test-secret-key"
os.environ["DATABASE_URI"] = f"sqlite:///{_DB_PATH}"

# Make sure the project root is importable regardless of pytest's rootdir.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Neutralise the auto-scrape scheduler before app import so no thread/network.
import services.scraper as _scraper_module  # noqa: E402

_scraper_module.start_auto_scheduler = lambda app: None

import app as app_module  # noqa: E402
from models import db, Submitter, Venue, Event, VenueAccessibility, ScrapedEvent  # noqa: E402
from services.limits import limiter as _limiter  # noqa: E402

app_module.app.config.update(
    TESTING=True,
    WTF_CSRF_ENABLED=False,
    # The test client speaks plain http; Secure cookies would never be sent.
    SESSION_COOKIE_SECURE=False,
)
# Tests hammer /login far past the brute-force limit. RATELIMIT_ENABLED in
# config is only read during init_app, so flip the live attribute instead.
_limiter.enabled = False


def pytest_unconfigure(config):
    try:
        os.unlink(_DB_PATH)
    except OSError:
        pass


@pytest.fixture
def app(tmp_path):
    flask_app = app_module.app
    # Per-test upload folder so flyer uploads never touch the real tree.
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    flask_app.config["UPLOAD_FOLDER"] = str(upload_dir)

    with flask_app.app_context():
        db.drop_all()
        db.create_all()
        try:
            yield flask_app
        finally:
            db.session.remove()
            db.drop_all()
            # Caching is process-global; clear it between tests.
            from services.cache import cache

            cache.clear()


@pytest.fixture
def client(app):
    return app.test_client()


# --- Model factories --------------------------------------------------------


@pytest.fixture
def make_user(app):
    def _make(email="user@example.com", password="password123", is_admin=False):
        user = Submitter(email=email)
        user.is_admin = is_admin
        if password:
            user.set_password(password)
        db.session.add(user)
        db.session.commit()
        return user

    return _make


@pytest.fixture
def admin(make_user):
    return make_user(email="admin@example.com", password="adminpass", is_admin=True)


@pytest.fixture
def make_venue(app):
    def _make(name="Kasheme", city="Zürich", plz="8001", canton="ZH", **kw):
        venue = Venue(name=name, city=city, plz=plz, canton=canton, **kw)
        db.session.add(venue)
        db.session.commit()
        return venue

    return _make


@pytest.fixture
def make_event(app, make_venue):
    from datetime import datetime, time, timedelta
    from services.events import compute_event_hash

    def _make(
        name="Test Show",
        venue=None,
        days_from_now=10,
        genre="Punk",
        acts="Band A, Band B",
        **kw,
    ):
        if venue is None:
            venue = make_venue()
        date = datetime.now() + timedelta(days=days_from_now)
        doors = time(19, 0)
        ev = Event(
            name=name,
            date=date,
            doors=doors,
            genre=genre,
            acts=acts,
            ticket_price=kw.pop("ticket_price", "20"),
            ticket_link=kw.pop("ticket_link", ""),
            venue_id=venue.id,
            event_hash=compute_event_hash(
                name, date, doors, genre, acts, "", "20", venue.id
            ),
            **kw,
        )
        db.session.add(ev)
        db.session.commit()
        return ev

    return _make


@pytest.fixture
def login(client):
    """Log a user in by setting the session cookie directly."""

    def _login(user):
        with client.session_transaction() as sess:
            sess["user_id"] = user.id
        return user

    return _login
