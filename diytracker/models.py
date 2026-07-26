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
    """Naive UTC now — for *audit* columns (created_at, updated_at, first_seen,
    last_seen, token expiries), which store UTC.

    Not for wall-clock columns. Event.date/Event.doors/Event.end_date hold the
    local Swiss time a show actually starts, exactly as entered, and must be
    compared against datetime.now() — comparing them to this would treat every
    event as ending an hour or two late.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


@sa_event.listens_for(Engine, "connect")
def _sqlite_pragmas(dbapi_connection, _connection_record):
    # WAL + busy timeout so concurrent gunicorn workers don't hit
    # "database is locked" on simultaneous writes. foreign_keys is off by
    # default in SQLite and must be enabled per connection; without it a
    # venue delete can orphan events (event.venue becomes None and the
    # event page 500s).
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=15000")
        cursor.execute("PRAGMA foreign_keys=ON")
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
    # Opt-out email when an admin edits an event carrying one of the user's
    # labels. Default on: the userbase is tiny and invited. server_default
    # keeps existing rows valid (see scripts/migrate_add_notify_flag.py).
    notify_label_events = db.Column(
        db.Boolean, nullable=False, default=True, server_default=db.text("1")
    )

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
    # Plain text, shown on the public label page's hero header.
    description = db.Column(db.Text, nullable=True)
    # Public profile links, all optional; URLs are SafeLink-validated at the
    # form and re-checked with is_safe_link() before rendering.
    website = db.Column(db.String(300), nullable=True)
    link_social_1 = db.Column(db.String(300), nullable=True)
    link_social_2 = db.Column(db.String(300), nullable=True)
    contact_email = db.Column(db.String(200), nullable=True)
    promoter_id = db.Column(db.Integer, db.ForeignKey("submitter.id"), nullable=False)
    promoter = db.relationship("Submitter", backref=db.backref("labels", lazy=True))
    created_at = db.Column(db.DateTime, nullable=True, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    def __repr__(self):
        return f"<Label {self.id}: {self.name} ({self.slug})>"


class PageText(db.Model):
    """Admin-editable intro paragraph for a landing page, one row per locale.
    kind is 'canton' or 'genre'; key is the page slug (zuerich, metal, ...).
    Lookup falls back to the default locale; no row means no paragraph."""

    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String(20), nullable=False)
    key = db.Column(db.String(120), nullable=False)
    locale = db.Column(db.String(5), nullable=False)
    text = db.Column(db.Text, nullable=False)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        db.UniqueConstraint("kind", "key", "locale", name="ux_page_text"),
    )

    def __repr__(self):
        return f"<PageText {self.kind}/{self.key} [{self.locale}]>"


class Genre(db.Model):
    """One selectable genre in the event form's picker. Replaces the old
    hardcoded list in forms.py: rows with added_by_id NULL are the curated
    seed set (only admins may delete them); user-added rows may be deleted
    by their creator or an admin. Event.genre stays a free-text string, so
    deleting a row never touches existing events."""

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    added_by_id = db.Column(db.Integer, db.ForeignKey("submitter.id"), nullable=True)
    added_by = db.relationship(
        "Submitter", backref=db.backref("genres_added", lazy=True)
    )
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    def __repr__(self):
        return f"<Genre {self.id}: {self.name}>"


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

    # Resolution tracking. status is authoritative: 'pending' (in the queue),
    # 'published' (approved into an Event) or 'rejected' (removed without
    # publishing, optionally with a reason). The legacy `approved` boolean
    # conflated the last two (approved with no approved_event_id meant
    # rejected); it is still written alongside status for one release as a
    # safety net for stray scripts, then write-only-status. approved_at is
    # the resolution time for both outcomes (local clock, kept for
    # consistency with existing rows). See scripts/migrate_add_queue_status.py.
    STATUS_PENDING = "pending"
    STATUS_PUBLISHED = "published"
    STATUS_REJECTED = "rejected"

    status = db.Column(
        db.String(20),
        nullable=False,
        default=STATUS_PENDING,
        server_default=STATUS_PENDING,
        index=True,
    )
    reject_reason = db.Column(db.String(300), nullable=True)
    approved = db.Column(db.Boolean, nullable=False, default=False)
    approved_at = db.Column(db.DateTime, nullable=True)
    approved_event_id = db.Column(db.Integer, db.ForeignKey("event.id"), nullable=True)

    # Stricter dedup for messy sources (konzibot): flagged when the event
    # collides on date + city/venue/title with something already on the
    # calendar or in the queue. Stays in the queue but is marked as a possible
    # duplicate so an admin looks twice before approving. review_reason holds a
    # human-readable summary of what it collided with, captured at ingest time.
    # server_default keeps existing rows valid without an Alembic migration
    # (schema is db.create_all(); see scripts/migrate_add_needs_review.py).
    needs_review = db.Column(
        db.Boolean, nullable=False, default=False, server_default=db.text("0")
    )
    review_reason = db.Column(db.String(300), nullable=True)

    # Metadata. server_default rather than default=utcnow because these rows are
    # also written by bulk inserts that bypass the ORM; SQLite's now() is UTC
    # too, so the stored value means the same thing either way.
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


class IpHostname(db.Model):
    """Cached reverse-DNS answers for the admin traffic views.

    A PTR lookup is a network round trip, so the hosts breakdown would either
    block the TUI or hammer the resolver on every refresh. Answers are kept
    here instead, failures included: hostname NULL means "asked, no PTR", and
    checked_at (UTC, audit-column clock) is what makes an answer expire.
    """

    ip = db.Column(db.String(45), primary_key=True)  # v6 max length
    hostname = db.Column(db.String(255), nullable=True)
    checked_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    def __repr__(self):
        return f"<IpHostname {self.ip}: {self.hostname or '(none)'}>"


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


class ActionLog(db.Model):
    """Append-only record of moderation and promoter actions, written via
    services/audit.py. target_id is deliberately not a foreign key: log
    rows must outlive the events, queue entries and labels they describe.
    actor is a snapshot (email, or "tui" for the admin tool) so the row
    stays readable after the account is deleted."""

    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)  # UTC
    actor_id = db.Column(db.Integer, db.ForeignKey("submitter.id"), nullable=True)
    actor = db.Column(db.String(200), nullable=False)
    action = db.Column(db.String(50), nullable=False, index=True)
    target_type = db.Column(db.String(20), nullable=False)
    target_id = db.Column(db.Integer, nullable=False, index=True)
    detail = db.Column(db.Text, nullable=True)

    def __repr__(self):
        return f"<ActionLog {self.action} {self.target_type}={self.target_id} by {self.actor}>"


def _format_parent_genres(genre):
    parents = _compute_parent_genres(genre)
    return "," + ",".join(parents) + "," if parents else None


@sa_event.listens_for(Event, "before_insert")
@sa_event.listens_for(Event, "before_update")
def _sync_event_parent_genres(mapper, connection, target):
    target.parent_genres = _format_parent_genres(target.genre)
