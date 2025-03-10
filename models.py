from flask_sqlalchemy import SQLAlchemy
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
    acts = db.Column(db.Text, nullable=True)
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

    def __init__(self, email, submission_code=None):
        self.email = email
        # We can generate a code if not provided
        self.submission_code = submission_code or str(uuid.uuid4())

    def __repr__(self):
        return f"{self.email} ({self.submission_code})"