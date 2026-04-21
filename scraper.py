import importlib.util
import os
import threading
from datetime import datetime

from models import db, Event, ScrapedEvent
from utils import resolve_canton

LAST_SCRAPE_FILE = os.path.join('instance', 'last_scrape.txt')
_scrape_lock = threading.Lock()
_scrape_running = False
_scrape_progress = {'total': 0, 'processed': 0, 'started_at': None, 'phase': ''}


def get_last_scrape_time():
    try:
        with open(LAST_SCRAPE_FILE) as f:
            return datetime.fromisoformat(f.read().strip())
    except (FileNotFoundError, ValueError):
        return None


def set_last_scrape_time(dt):
    os.makedirs(os.path.dirname(LAST_SCRAPE_FILE), exist_ok=True)
    with open(LAST_SCRAPE_FILE, 'w') as f:
        f.write(dt.isoformat())


def _load_scraper():
    """Load the scrape_events module from utils/ directory via importlib."""
    spec = importlib.util.spec_from_file_location(
        "scrape_events",
        os.path.join(os.path.dirname(__file__), "utils", "scrape_events.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _get_known_urls(app):
    """Return a set of all URLs already in the queue or calendar."""
    with app.app_context():
        scraped_urls = {r.url for r in ScrapedEvent.query.with_entities(ScrapedEvent.url).all() if r.url}
        event_urls = {r.source_url for r in Event.query.with_entities(Event.source_url).filter(Event.source_url.isnot(None)).all()}
        return scraped_urls | event_urls


def run_scrape(app):
    """Run scrapers and insert only new events into the ScrapedEvent table.

    Accepts the Flask app instance to push an app context for DB access.
    """
    global _scrape_running, _scrape_progress
    try:
        scraper_mod = _load_scraper()
        get_sitemap_event_urls = scraper_mod.get_sitemap_event_urls
        get_petzi_event_urls = scraper_mod.get_petzi_event_urls
        parse_metalgigs_event = scraper_mod.parse_metalgigs_event
        parse_petzi_event = scraper_mod.parse_petzi_event
        from scripts.import_scraped_events import parse_date, parse_time

        _scrape_progress = {'total': 0, 'processed': 0, 'started_at': datetime.now().isoformat(), 'phase': 'Fetching sitemaps'}

        mg_urls = get_sitemap_event_urls("https://metalgigs.ch/sitemap.xml", "/konzerte/")
        petzi_urls = get_sitemap_event_urls("https://www.petzi.ch/en/sitemap.xml", "/en/events/")
        if not petzi_urls:
            petzi_urls = get_petzi_event_urls()

        # Filter out URLs already in the queue or calendar before making any HTTP requests
        known_urls = _get_known_urls(app)
        all_urls = [
            ('metalgigs', url, parse_metalgigs_event) for url in mg_urls if url not in known_urls
        ] + [
            ('petzi', url, parse_petzi_event) for url in petzi_urls if url not in known_urls
        ]

        app.logger.info(f"Scrape: {len(mg_urls) + len(petzi_urls)} total URLs, {len(all_urls)} new after dedup")
        _scrape_progress['total'] = len(all_urls)
        _scrape_progress['phase'] = 'Scraping events'

        events = []
        for _source, url, parser in all_urls:
            parsed = parser(url)
            if parsed:
                events.append(parsed)
            _scrape_progress['processed'] += 1

        _scrape_progress['phase'] = 'Importing to database'
        with app.app_context():
            # Re-fetch known URLs inside the app context for the safety check
            known_urls_now = _get_known_urls(app)
            count = 0
            for row in events:
                url = row.get('url')
                if url and url in known_urls_now:
                    continue
                region = resolve_canton(row.get('region') or '', row.get('city') or '')
                scraped = ScrapedEvent(
                    source=row.get('source'),
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
                    region=region,
                    postal_code=row.get('postal_code'),
                    ticket_price=row.get('ticket_price'),
                    ticket_currency=row.get('ticket_currency'),
                    ticket_url=row.get('ticket_url'),
                    organizer=row.get('organizer'),
                    event_status=row.get('event_status'),
                )
                db.session.add(scraped)
                count += 1
            db.session.commit()
            app.logger.info(f"Scrape complete: imported {count} new events")
        set_last_scrape_time(datetime.now())
    except Exception:
        app.logger.exception("Scrape failed")
    finally:
        with _scrape_lock:
            _scrape_running = False
