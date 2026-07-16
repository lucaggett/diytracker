"""Shared pytest fixtures.

The app is built once per session via the factory (`create_app(TestConfig)`),
which never loads .env, never starts the scraper thread, and applies the test
overrides (CSRF off, rate limiter off, scrape detector off) through config.

Schema is rebuilt per-test (drop_all/create_all) so tests are isolated.
"""

import os
import sys
import tempfile

import pytest

# Make sure the project root is importable regardless of pytest's rootdir.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from diytracker.app import create_app  # noqa: E402
from diytracker.config import TestConfig  # noqa: E402
from diytracker.models import db, Submitter, Venue, Event  # noqa: E402

# A shared temp-file DB (rather than sqlite:///:memory:) so every connection
# from the pool sees the same database.
_DB_FD, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="diytracker-test-")
os.close(_DB_FD)

_app = create_app(TestConfig(f"sqlite:///{_DB_PATH}"))

# Tests hammer /login far past the brute-force limit. Disabled via the live
# attribute (not RATELIMIT_ENABLED) so the rate-limit test can flip it back
# on for one request burst — config-level disable can't be re-enabled at
# runtime.
from diytracker.services.limits import limiter as _limiter  # noqa: E402

_limiter.enabled = False


def pytest_unconfigure(config):
    try:
        os.unlink(_DB_PATH)
    except OSError:
        pass


@pytest.fixture
def app(tmp_path):
    flask_app = _app
    # Per-test upload folder so flyer uploads never touch the real tree.
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    flask_app.config["UPLOAD_FOLDER"] = str(upload_dir)

    with flask_app.app_context():
        db.drop_all()
        db.create_all()
        # The app factory seeds at startup; per-test schema rebuilds need to
        # re-seed so the genre catalog matches a real boot.
        from diytracker.services.genre_catalog import seed_genres

        seed_genres()
        try:
            yield flask_app
        finally:
            db.session.remove()
            db.drop_all()
            # Caching is process-global; clear it between tests.
            from diytracker.services.cache import cache

            cache.clear()


@pytest.fixture
def client(app):
    test_client = app.test_client()
    # Tests assert English flash messages; without this the locale falls back
    # to the default ('de') and the assertions would hit translated strings.
    test_client.environ_base["HTTP_ACCEPT_LANGUAGE"] = "en"
    return test_client


# --- Model factories --------------------------------------------------------


@pytest.fixture
def make_user(app):
    def _make(
        email="user@example.com",
        password="password123",
        is_admin=False,
        is_promoter=False,
    ):
        user = Submitter(email=email)
        user.is_admin = is_admin
        user.is_promoter = is_promoter
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
    from diytracker.services.events import compute_event_hash

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
def make_label(app):
    from diytracker.models import Label
    from diytracker.services.labels import unique_slug

    def _make(promoter, name="Cool Label", **kw):
        label = Label(name=name, slug=unique_slug(name), promoter_id=promoter.id, **kw)
        db.session.add(label)
        db.session.commit()
        return label

    return _make


@pytest.fixture
def login(client):
    """Log a user in by setting the session cookie directly."""

    def _login(user):
        with client.session_transaction() as sess:
            sess["user_id"] = user.id
        return user

    return _login
