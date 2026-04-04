import secrets
from datetime import datetime, timedelta

from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
import uuid

db = SQLAlchemy()

class Event(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    event_hash = db.Column(db.String(100), nullable=False, unique=True)
    name = db.Column(db.String(100), nullable=False)
    date = db.Column(db.DateTime, nullable=False)
    # Add a foreign key to the Venue model
    venue_id = db.Column(db.Integer, db.ForeignKey('venue.id'), nullable=False)
    venue = db.relationship('Venue', backref=db.backref('events', lazy=True))
    ticket_price = db.Column(db.String(50), nullable=False)
    ticket_link = db.Column(db.String(200), nullable=True)
    doors = db.Column(db.Time, nullable=False)  # Storing door time as 24-hour format
    genre = db.Column(db.String(100), nullable=True)
    end_date = db.Column(db.Date, nullable=True)
    is_festival = db.Column(db.Boolean, nullable=False, default=False)
    acts = db.Column(db.Text, nullable=True)
    description = db.Column(db.Text, nullable=True)
    source_url  = db.Column(db.String(300), nullable=True)
    flyer = db.Column(db.String(200), nullable=True)  # File path to the uploaded flyer
    submitter_id = db.Column(db.Integer, db.ForeignKey('submitter.id'), nullable=True)
    submitter = db.relationship('Submitter', backref=db.backref('events', lazy=True))

    def __repr__(self):
        return f'<Event {self.id}: {self.name} @ {self.date} added by {self.submitter}>'


class Venue(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    address = db.Column(db.String(200), nullable=True)
    city = db.Column(db.String(100), nullable=False)
    canton = db.Column(db.String(100), nullable=True)
    plz = db.Column(db.String(10), nullable=False)
    coords = db.Column(db.String(50), nullable=True)  # Storing coordinates as string

    def __repr__(self):
        return f'<Venue {self.id}: {self.name} in {self.city}>'

class Submitter(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(100), nullable=False)
    submission_code = db.Column(db.String(100), unique=True, nullable=True)
    password_hash = db.Column(db.String(256), nullable=True)
    is_admin = db.Column(db.Boolean, nullable=False, default=False)
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
        self.invite_token_expiry = datetime.utcnow() + timedelta(days=7)
        return self.invite_token

    def clear_invite_token(self):
        self.invite_token = None
        self.invite_token_expiry = None

    def __repr__(self):
        return f"{self.email} ({self.submission_code})"


class ScrapedEvent(db.Model):
    """Database model storing events scraped from external sources."""
    # Primary key
    id = db.Column(db.Integer, primary_key=True)

    # Scraped metadata
    source = db.Column(db.String(20), nullable=True)
    url = db.Column(db.String(300), nullable=True)
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

    # Approval tracking
    approved = db.Column(db.Boolean, nullable=False, default=False)
    approved_at = db.Column(db.DateTime, nullable=True)
    approved_event_id = db.Column(db.Integer, db.ForeignKey('event.id'), nullable=True)

    # Metadata
    created_at = db.Column(db.DateTime, nullable=False, server_default=db.func.now())

    def __repr__(self):
        return f"<ScrapedEvent {self.id}: {self.title} on {self.start_date}>"