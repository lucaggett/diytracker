from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

class Event(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    date = db.Column(db.DateTime, nullable=False)
    venue = db.Column(db.String(200), nullable=False)
    doors = db.Column(db.Time, nullable=False)  # Storing door time as 24-hour format
    genre = db.Column(db.String(100), nullable=True)
    acts = db.Column(db.Text, nullable=True)
    flyer = db.Column(db.String(200), nullable=True)  # File path to the uploaded flyer

    def __repr__(self):
        return f'<Event {self.id}: {self.name} @ {self.date}>'
