import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import event as sa_event
from sqlalchemy.engine import Engine
from werkzeug.security import generate_password_hash, check_password_hash
import uuid

from diytracker.utils import parent_genres as _compute_parent_genres

db = SQLAlchemy()


def utcnow():
    """Naive UTC now — DateTime columns store naive values, so all stored
    timestamps and comparisons must use this instead of datetime.now()."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


@sa_event.listens_for(Engine, "connect")
def _sqlite_pragmas(dbapi_connection, _connection_record):
    # WAL + busy timeout so concurrent gunicorn workers don't hit
    # "database is locked" on simultaneous writes.
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=15000")
        cursor.close()


class Event(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    event_hash = db.Column(db.String(100), nullable=False, unique=True)
    name = db.Column(db.String(100), nullable=False)
    date = db.Column(db.DateTime, nullable=False)
    # Add a foreign key to the Venue model
    venue_id = db.Column(db.Integer, db.ForeignKey("venue.id"), nullable=False)
    venue = db.relationship("Venue", backref=db.backref("events", lazy=True))
    ticket_price = db.Column(db.String(50), nullable=False)
    ticket_link = db.Column(db.String(200), nullable=True)
    doors = db.Column(db.Time, nullable=False)  # Storing door time as 24-hour format
    genre = db.Column(db.String(100), nullable=True)
    # Comma-joined parent genres derived from `genre`, with leading and
    # trailing commas as sentinels (e.g. ',Metal,Hardcore,'). Auto-synced via
    # the before_insert/before_update hook below; never set this column directly.
    parent_genres = db.Column(db.String(200), nullable=True, index=True)
    end_date = db.Column(db.Date, nullable=True)
    is_festival = db.Column(db.Boolean, nullable=False, default=False)
    acts = db.Column(db.Text, nullable=True)
    description = db.Column(db.Text, nullable=True)
    source_url = db.Column(db.String(300), nullable=True)
    flyer = db.Column(db.String(200), nullable=True)  # File path to the uploaded flyer
    submitter_id = db.Column(db.Integer, db.ForeignKey("submitter.id"), nullable=True)
    submitter = db.relationship("Submitter", backref=db.backref("events", lazy=True))
    label_id = db.Column(
        db.Integer, db.ForeignKey("label.id"), nullable=True, index=True
    )
    label = db.relationship("Label", backref=db.backref("events", lazy=True))
    # scheduled/cancelled/postponed — drives schema.org eventStatus and the
    # visible badge on the event page.
    status = db.Column(db.String(20), nullable=False, default="scheduled")
    # Nullable: SQLite can't ALTER TABLE ADD COLUMN with a non-constant NOT
    # NULL default; migrations/migrate_add_created_at.py backfills old rows.
    created_at = db.Column(db.DateTime, nullable=True, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    def __repr__(self):
        return f"<Event {self.id}: {self.name} @ {self.date} added by {self.submitter}>"


class Venue(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    address = db.Column(db.String(200), nullable=True)
    city = db.Column(db.String(100), nullable=False)
    canton = db.Column(db.String(100), nullable=True)
    plz = db.Column(db.String(10), nullable=False)
    coords = db.Column(db.String(50), nullable=True)
    accessibility_token = db.Column(db.String(64), nullable=True, unique=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    def generate_accessibility_token(self):
        self.accessibility_token = secrets.token_urlsafe(32)
        return self.accessibility_token

    def __repr__(self):
        return f"<Venue {self.id}: {self.name} in {self.city}>"


class VenueAccessibility(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    venue_id = db.Column(
        db.Integer, db.ForeignKey("venue.id"), nullable=False, unique=True
    )
    updated_at = db.Column(db.DateTime, nullable=False)

    # Mobility & Wheelchair
    step_free_entrance = db.Column(db.String(20), nullable=True)  # yes/partial/no
    step_free_entrance_notes = db.Column(db.Text, nullable=True)
    step_free_interior = db.Column(db.String(20), nullable=True)  # yes/partial/no
    accessible_toilet = db.Column(db.String(20), nullable=True)  # yes/partial/no
    accessible_toilet_notes = db.Column(db.Text, nullable=True)
    wheelchair_spaces = db.Column(db.String(5), nullable=True)  # yes/no
    wheelchair_spaces_count = db.Column(db.Integer, nullable=True)
    floor_surface = db.Column(db.String(50), nullable=True)

    # Sensory, Epilepsy & Autism
    strobe_lights = db.Column(db.String(20), nullable=True)  # never/sometimes/regularly
    strobe_warning = db.Column(db.String(20), nullable=True)  # always/sometimes/never
    smoke_machines = db.Column(
        db.String(20), nullable=True
    )  # never/sometimes/regularly
    sound_level = db.Column(db.String(20), nullable=True)
    quiet_space = db.Column(db.String(5), nullable=True)  # yes/no
    earplugs_available = db.Column(db.String(5), nullable=True)  # yes/no
    sensory_friendly_events = db.Column(
        db.String(20), nullable=True
    )  # yes/sometimes/no

    # Hearing
    hearing_loop = db.Column(db.String(20), nullable=True)  # yes/no/unknown
    sign_language = db.Column(db.String(20), nullable=True)  # sometimes/rarely/never

    # Medical
    medication_fridge = db.Column(db.String(20), nullable=True)  # yes/no/ask_staff
    first_aid_kit = db.Column(db.String(5), nullable=True)  # yes/no
    aed_on_site = db.Column(db.String(20), nullable=True)  # yes/no/unknown

    # General / Social
    accessible_parking = db.Column(db.String(20), nullable=True)  # yes/nearby/no
    public_transport_notes = db.Column(db.Text, nullable=True)
    gender_neutral_toilets = db.Column(db.String(5), nullable=True)  # yes/no
    seating_areas = db.Column(db.String(5), nullable=True)  # yes/no
    guide_dogs_welcome = db.Column(db.String(5), nullable=True)  # yes/no
    quiet_entrance = db.Column(db.String(5), nullable=True)  # yes/no
    additional_notes = db.Column(db.Text, nullable=True)

    venue = db.relationship("Venue", backref=db.backref("accessibility", uselist=False))


class Submitter(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(100), nullable=False, unique=True)
    submission_code = db.Column(db.String(100), unique=True, nullable=True)
    password_hash = db.Column(db.String(256), nullable=True)
    is_admin = db.Column(db.Boolean, nullable=False, default=False)
    is_promoter = db.Column(db.Boolean, nullable=False, default=False)
    invite_token = db.Column(db.String(64), nullable=True, unique=True)
    invite_token_expiry = db.Column(db.DateTime, nullable=True)

    def __init__(self, email, submission_code=None):
        self.email = email
        self.submission_code = submission_code or str(uuid.uuid4())

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        if not self.password_hash:
            return False
        return check_password_hash(self.password_hash, password)

    def generate_invite_token(self):
        self.invite_token = secrets.token_urlsafe(32)
        self.invite_token_expiry = utcnow() + timedelta(days=7)
        return self.invite_token

    def clear_invite_token(self):
        self.invite_token = None
        self.invite_token_expiry = None

    def __repr__(self):
        return f"{self.email} ({self.submission_code})"


class Label(db.Model):
    """A record label / collective owned by a promoter account. Events can
    optionally carry a label; each label gets a public /label/<slug>/ page."""

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    # Stored (not derived like genre slugs): names are arbitrary user input,
    # so collisions get -2/-3 suffixes at write time. Regenerated on rename.
    slug = db.Column(db.String(120), nullable=False, unique=True, index=True)
    logo = db.Column(db.String(200), nullable=True)  # same convention as Event.flyer
    promoter_id = db.Column(db.Integer, db.ForeignKey("submitter.id"), nullable=False)
    promoter = db.relationship("Submitter", backref=db.backref("labels", lazy=True))
    created_at = db.Column(db.DateTime, nullable=True, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    def __repr__(self):
        return f"<Label {self.id}: {self.name} ({self.slug})>"


class ScrapedEvent(db.Model):
    """Database model storing events scraped from external sources."""

    # Primary key
    id = db.Column(db.Integer, primary_key=True)

    # Scraped metadata
    source = db.Column(db.String(20), nullable=True)
    url = db.Column(db.String(300), nullable=True, unique=True)
    title = db.Column(db.String(200), nullable=True)
    performers = db.Column(db.Text, nullable=True)
    styles = db.Column(db.String(200), nullable=True)
    description = db.Column(db.Text, nullable=True)
    start_date = db.Column(db.Date, nullable=True)
    end_date = db.Column(db.Date, nullable=True)
    doors_open = db.Column(db.Time, nullable=True)
    start_time = db.Column(db.Time, nullable=True)
    venue_name = db.Column(db.String(200), nullable=True)
    street_address = db.Column(db.String(200), nullable=True)
    city = db.Column(db.String(100), nullable=True)
    region = db.Column(db.String(100), nullable=True)
    postal_code = db.Column(db.String(20), nullable=True)
    ticket_price = db.Column(db.String(50), nullable=True)
    ticket_currency = db.Column(db.String(10), nullable=True)
    ticket_url = db.Column(db.String(300), nullable=True)
    organizer = db.Column(db.String(200), nullable=True)
    event_status = db.Column(db.String(100), nullable=True)

    # Multi-source ingest: flyer image + identity for sources without URLs
    flyer = db.Column(db.String(200), nullable=True)  # File path to the stored flyer
    source_id = db.Column(db.String(64), nullable=True)  # Stable per-source record id
    submitter = db.Column(db.String(200), nullable=True)  # Who posted it at the source

    __table_args__ = (
        db.UniqueConstraint(
            "source", "source_id", name="ux_scraped_event_source_source_id"
        ),
    )

    # Approval tracking
    approved = db.Column(db.Boolean, nullable=False, default=False)
    approved_at = db.Column(db.DateTime, nullable=True)
    approved_event_id = db.Column(db.Integer, db.ForeignKey("event.id"), nullable=True)

    # Metadata
    created_at = db.Column(db.DateTime, nullable=False, server_default=db.func.now())

    def __repr__(self):
        return f"<ScrapedEvent {self.id}: {self.title} on {self.start_date}>"


class SkippedUrl(db.Model):
    """URLs the scraper fetched and rejected (non-concert petzi rows, invalid
    payloads). Without a record of these, every scrape run re-downloads the
    same rejected pages; the scraper folds them into its known-URL set."""

    id = db.Column(db.Integer, primary_key=True)
    url = db.Column(db.String(300), nullable=False, unique=True)
    source = db.Column(db.String(20), nullable=True)
    reason = db.Column(db.String(200), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, server_default=db.func.now())

    def __repr__(self):
        return f"<SkippedUrl {self.url}: {self.reason}>"


class ScrapeSuspect(db.Model):
    """IPs flagged by services/scrape_detection.py as likely scrapers.
    Detection-only: rows are evidence for the admin view, nothing is blocked."""

    id = db.Column(db.Integer, primary_key=True)
    ip = db.Column(db.String(45), nullable=False, unique=True)  # v6 max length
    first_seen = db.Column(db.DateTime, nullable=False, default=utcnow)
    last_seen = db.Column(db.DateTime, nullable=False, default=utcnow)
    score = db.Column(db.Float, nullable=False, default=0.0)
    request_count = db.Column(db.Integer, nullable=False, default=0)
    signals = db.Column(db.Text, nullable=True)  # JSON list of signal names
    user_agent = db.Column(db.String(300), nullable=True)
    sample_paths = db.Column(db.Text, nullable=True)  # JSON list of paths

    def __repr__(self):
        return f"<ScrapeSuspect {self.ip} score={self.score}>"


class EventDailyViews(db.Model):
    """Per-day hit/visitor counts for /events/<id>/ pages, distilled from
    nginx access logs by services/event_views.py on the analytics schedule.
    event_id is deliberately not a foreign key: log lines may reference
    events that were deleted later, and rows must outlive the log window
    (days that rotate out of the logs are never recomputed)."""

    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, nullable=False, index=True)
    date = db.Column(db.Date, nullable=False)
    hits = db.Column(db.Integer, nullable=False, default=0)
    visitors = db.Column(db.Integer, nullable=False, default=0)  # distinct IPs

    __table_args__ = (
        db.UniqueConstraint("event_id", "date", name="ux_event_daily_views"),
    )

    def __repr__(self):
        return f"<EventDailyViews event={self.event_id} {self.date}: {self.hits}>"


def _format_parent_genres(genre):
    parents = _compute_parent_genres(genre)
    return "," + ",".join(parents) + "," if parents else None


@sa_event.listens_for(Event, "before_insert")
@sa_event.listens_for(Event, "before_update")
def _sync_event_parent_genres(mapper, connection, target):
    target.parent_genres = _format_parent_genres(target.genre)
