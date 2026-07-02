"""
Import scraped events from a CSV file into the database.

This script reads the CSV produced by `scrape_events.py` and populates
the `ScrapedEvent` table defined in `scraped_event_model.py`.  Run
this script after scraping to make scraped events available for
approval without re-reading the CSV on every request.

Usage::

    python scripts/import_scraped_events.py path/to/events.csv

If no path is provided, it defaults to `events.csv` in the project root.
"""

import os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import csv
from datetime import datetime, time
from flask import Flask

from models import db, ScrapedEvent
from utils import clean_genre_tokens, clean_ticket_url, resolve_canton

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def parse_date(date_str: str):
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except Exception:
        return None


def parse_time(time_str: str):
    try:
        return datetime.strptime(time_str, "%H:%M").time()
    except Exception:
        return None


def import_events(csv_path: str, db_uri: str = "sqlite:///events.db"):
    # Initialize Flask app context for SQLAlchemy
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = db_uri
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db.init_app(app)
    with app.app_context():
        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            count = 0
            for row in reader:
                source = row.get("source")
                url = row.get("url")
                # Skip if this event already exists
                existing = ScrapedEvent.query.filter_by(source=source, url=url).first()
                if existing:
                    continue
                raw_styles = row.get("styles") or ""
                # petzi sitemap mixes concerts with theatre/workshop/club-night
                # rows; the raw 'concert' token is the only signal, so filter
                # on it before cleanup strips the token.
                if source == "petzi" and "concert" not in raw_styles.lower():
                    continue
                region = resolve_canton(row.get("region") or "", row.get("city") or "")
                cleaned_styles = ", ".join(clean_genre_tokens(raw_styles)) or None
                cleaned_ticket_url = clean_ticket_url(row.get("ticket_url"), source)
                event = ScrapedEvent(
                    source=source,
                    url=url,
                    title=row.get("title"),
                    performers=row.get("performers"),
                    styles=cleaned_styles,
                    description=row.get("description"),
                    start_date=parse_date(row.get("start_date") or ""),
                    end_date=parse_date(row.get("end_date") or ""),
                    doors_open=parse_time(row.get("doors_open") or ""),
                    start_time=parse_time(row.get("start_time") or ""),
                    venue_name=row.get("venue_name"),
                    street_address=row.get("street_address"),
                    city=row.get("city"),
                    region=region,
                    postal_code=row.get("postal_code"),
                    ticket_price=row.get("ticket_price"),
                    ticket_currency=row.get("ticket_currency"),
                    ticket_url=cleaned_ticket_url,
                    organizer=row.get("organizer"),
                    event_status=row.get("event_status"),
                )
                db.session.add(event)
                count += 1
            db.session.commit()
            print(f"Imported {count} events from {csv_path} into the database.")


if __name__ == "__main__":
    csv_file = (
        sys.argv[1]
        if len(sys.argv) > 1
        else os.path.join(PROJECT_ROOT, "instance", "events.csv")
    )
    import_events(csv_file)
