from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

class Event(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    date = db.Column(db.DateTime, nullable=False)
    venue_name = db.Column(db.String(200), nullable=False)
    venue_address = db.Column(db.String(200), nullable=True)
    venue_city = db.Column(db.String(100), nullable=False)
    venue_canton = db.Column(db.String(100), nullable=True)
    venue_plz = db.Column(db.String(10), nullable=False)
    ticket_price = db.Column(db.String(50), nullable=False)
    ticket_link = db.Column(db.String(200), nullable=True)
    venue_coords = db.Column(db.String(50), nullable=True)  # Storing coordinates as string
    doors = db.Column(db.Time, nullable=False)  # Storing door time as 24-hour format
    genre = db.Column(db.String(100), nullable=True)
    acts = db.Column(db.Text, nullable=True)
    flyer = db.Column(db.String(200), nullable=True)  # File path to the uploaded flyer


    def __repr__(self):
        return f'<Event {self.id}: {self.name} @ {self.date}>'


class Submitter(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(100), nullable=False)
