"""
Import scraped events from a CSV file into the database.

This script reads the CSV produced by `scrape_events.py` and populates
the `ScrapedEvent` table defined in `scraped_event_model.py`.  Run
this script after scraping to make scraped events available for
approval without re-reading the CSV on every request.

Usage::

    python import_scraped_events.py path/to/events.csv

If no path is provided, it defaults to `events.csv` in the current
directory.
"""
import csv
import sys
from datetime import datetime, time
from flask import Flask

from models import db, ScrapedEvent


def parse_date(date_str: str):
    try:
        return datetime.strptime(date_str, '%Y-%m-%d').date()
    except Exception:
        return None


def parse_time(time_str: str):
    try:
        return datetime.strptime(time_str, '%H:%M').time()
    except Exception:
        return None


def import_events(csv_path: str, db_uri: str = 'sqlite:///events.db'):
    # Initialize Flask app context for SQLAlchemy
    app = Flask(__name__)
    app.config['SQLALCHEMY_DATABASE_URI'] = db_uri
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    db.init_app(app)
    with app.app_context():
        with open(csv_path, newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            count = 0
            for row in reader:
                source = row.get('source')
                url = row.get('url')
                # Skip if this event already exists
                existing = ScrapedEvent.query.filter_by(source=source, url=url).first()
                if existing:
                    continue
                event = ScrapedEvent(
                    source=source,
                    url=url,
                    title=row.get('title'),
                    performers=row.get('performers'),
                    styles=row.get('styles'),
                    description=row.get('description'),
                    start_date=parse_date(row.get('start_date') or ''),
                    end_date=parse_date(row.get('end_date') or ''),
                    doors_open=parse_time(row.get('doors_open') or ''),
                    start_time=parse_time(row.get('start_time') or ''),
                    venue_name=row.get('venue_name'),
                    street_address=row.get('street_address'),
                    city=row.get('city'),
                    region=row.get('region'),
                    postal_code=row.get('postal_code'),
                    ticket_price=row.get('ticket_price'),
                    ticket_currency=row.get('ticket_currency'),
                    ticket_url=row.get('ticket_url'),
                    organizer=row.get('organizer'),
                    event_status=row.get('event_status'),
                )
                db.session.add(event)
                count += 1
            db.session.commit()
            print(f"Imported {count} events from {csv_path} into the database.")


if __name__ == '__main__':
    csv_file = sys.argv[1] if len(sys.argv) > 1 else 'events.csv'
    import_events(csv_file)