# insert_dummy_events.py
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta, time
import random
import uuid

from app import app, db
from models import Event, Venue, Submitter

def get_or_create_venue(name, address, city, canton, plz, coords):
    v = Venue.query.filter_by(name=name, city=city).first()
    if v:
        # Optionally update fields if your schema allows drift
        return v
    v = Venue(
        name=name,
        address=address,
        city=city,
        canton=canton,
        plz=plz,
        coords=coords,
    )
    db.session.add(v)
    db.session.flush()  # ensures v.id is available without full commit
    return v

def get_or_create_submitter(email):
    s = Submitter.query.filter_by(email=email).first()
    if s:
        return s
    s = Submitter(email=email)  # generates a submission_code automatically
    db.session.add(s)
    db.session.flush()
    return s

with app.app_context():
    # Optional: Clear existing events for a clean slate
    Event.query.delete()
    db.session.commit()

    # Seed a small, realistic venue pool (created if missing)
    # Feel free to extend or swap these with your real venues
    venue_seeds = [
        {
            "name": "Hall A", "address": "10 Main Street", "city": "Zurich",
            "canton": "Zurich", "plz": "8001", "coords": "47.3769° N, 8.5417° E"
        },
        {
            "name": "Club B", "address": "22 River Road", "city": "Bern",
            "canton": "Bern", "plz": "3001", "coords": "46.9480° N, 7.4474° E"
        },
        {
            "name": "Theater C", "address": "3 Grand Ave", "city": "Geneva",
            "canton": "Geneva", "plz": "1201", "coords": "46.2044° N, 6.1432° E"
        },
        {
            "name": "Arena D", "address": "50 Lake Blvd", "city": "Basel",
            "canton": "Basel", "plz": "4001", "coords": "47.5596° N, 7.5886° E"
        },
        {
            "name": "Stadium E", "address": "77 Hill St", "city": "Lucerne",
            "canton": "Lucerne", "plz": "6003", "coords": "47.0502° N, 8.3093° E"
        },
    ]

    venues = [get_or_create_venue(**v) for v in venue_seeds]

    # Seed a few submitters (created if missing)
    submitter_emails = [
        "dummy1@example.com",
        "dummy2@example.com",
        "dummy3@example.com",
    ]
    submitters = [get_or_create_submitter(email) for email in submitter_emails]

    # Sample data for genres and acts
    genres = ['Rock', 'Jazz', 'Classical', 'Pop', 'Hip-Hop', 'Electronic', 'Folk', 'Blues']
    acts_list = [
        'Band Alpha', 'Artist Beta', 'Ensemble Gamma',
        'DJ Delta', 'Group Epsilon', 'Performer Zeta'
    ]

    flyer_path = "uploads/flyer.jpeg"

    # Start date is today
    start_date = datetime.now().date()

    # Number of events to create
    num_events = 100

    for i in range(num_events):
        event_date = start_date + timedelta(days=random.randint(0, 60))  # within next 60 days
        event_time = time(hour=random.randint(18, 22), minute=0)  # 18:00–22:00
        doors_time = (datetime.combine(event_date, event_time) - timedelta(hours=1)).time()  # 1h before

        venue = random.choice(venues)
        submitter = random.choice(submitters)
        acts = ', '.join(random.sample(acts_list, k=random.randint(1, 3)))  # 1–3 acts
        ticket_price = f"{random.randint(10, 100)} CHF"

        event = Event(
            event_hash=uuid.uuid4().hex,  # required + unique
            name=f'Dummy Event {i+1}',
            date=datetime.combine(event_date, event_time),
            venue_id=venue.id,
            ticket_price=ticket_price,
            ticket_link='http://example.com/tickets',
            doors=doors_time,
            genre=random.choice(genres),
            acts=acts,
            flyer=flyer_path,
            submitter_id=submitter.id,
        )

        db.session.add(event)

    db.session.commit()
    print(f"{num_events} dummy events inserted successfully.")
