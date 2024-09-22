# insert_dummy_events.py

from datetime import datetime, timedelta, time
import random
from app import app, db
from models import Event

with app.app_context():
    # Optional: Clear existing events for a clean slate
    Event.query.delete()
    db.session.commit()

    # Sample data for cantons, genres, and other fields
    cantons = ['Zurich', 'Bern', 'Geneva', 'Basel', 'Lucerne', 'Vaud', 'St. Gallen', 'Ticino']
    genres = ['Rock', 'Jazz', 'Classical', 'Pop', 'Hip-Hop', 'Electronic', 'Folk', 'Blues']
    venues = ['Hall A', 'Club B', 'Theater C', 'Arena D', 'Stadium E']
    cities = ['Zurich', 'Bern', 'Geneva', 'Basel', 'Lucerne', 'Lausanne', 'St. Gallen', 'Lugano']
    acts_list = [
        'Band Alpha', 'Artist Beta', 'Ensemble Gamma',
        'DJ Delta', 'Group Epsilon', 'Performer Zeta'
    ]

    # Start date is today
    start_date = datetime.now().date()

    # Number of events to create
    num_events = 100

    for i in range(num_events):
        event_date = start_date + timedelta(days=random.randint(0, 60))  # Events within the next 60 days
        event_time = time(hour=random.randint(18, 22), minute=0)  # Events start between 6 PM and 10 PM
        doors_time = (datetime.combine(event_date, event_time) - timedelta(hours=1)).time()  # Doors open 1 hour before

        canton = random.choice(cantons)
        genre = random.choice(genres)
        venue_name = random.choice(venues)
        city = random.choice(cities)
        acts = ', '.join(random.sample(acts_list, k=random.randint(1, 3)))  # 1 to 3 acts
        ticket_price = f"{random.randint(10, 100)} CHF"

        event = Event(
            name=f'Dummy Event {i+1}',
            date=datetime.combine(event_date, event_time),
            doors=doors_time,
            venue_name=venue_name,
            venue_address=f'{random.randint(1, 100)} Main Street',
            venue_city=city,
            venue_canton=canton,
            venue_plz=f'{random.randint(1000, 9999)}',
            ticket_price=ticket_price,
            ticket_link='http://example.com/tickets',
            venue_coords='47.3769° N, 8.5417° E',  # Example coordinates
            genre=genre,
            acts=acts,
            flyer=None  # You can add a path to a flyer image if needed
        )

        db.session.add(event)

    db.session.commit()
    print(f"{num_events} dummy events inserted successfully.")
