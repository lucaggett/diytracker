import importlib.util
import os
import threading
import time as time_module
from datetime import datetime

from models import db, Event, ScrapedEvent
from services.ingest import ingest_event

LAST_SCRAPE_FILE = os.path.join('instance', 'last_scrape.txt')
SCRAPE_INTERVAL_HOURS = 1

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


def is_running():
    return _scrape_running


def get_progress():
    return dict(_scrape_progress)


def _load_scraper():
    spec = importlib.util.spec_from_file_location(
        "scrape_events",
        os.path.join(os.path.dirname(os.path.dirname(__file__)), "utils", "scrape_events.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _get_known_urls():
    scraped_urls = {r.url for r in ScrapedEvent.query.with_entities(ScrapedEvent.url).all() if r.url}
    event_urls = {r.source_url for r in Event.query.with_entities(Event.source_url).filter(Event.source_url.isnot(None)).all()}
    return scraped_urls | event_urls


def _scrape_and_import(app):
    global _scrape_running, _scrape_progress
    try:
        scraper = _load_scraper()
        get_sitemap_event_urls = scraper.get_sitemap_event_urls
        get_petzi_event_urls = scraper.get_petzi_event_urls
        parse_metalgigs_event = scraper.parse_metalgigs_event
        parse_petzi_event = scraper.parse_petzi_event

        _scrape_progress = {'total': 0, 'processed': 0, 'started_at': datetime.now().isoformat(), 'phase': 'Fetching sitemaps'}

        mg_urls = get_sitemap_event_urls("https://metalgigs.ch/sitemap.xml", "/konzerte/")
        petzi_urls = get_sitemap_event_urls("https://www.petzi.ch/en/sitemap.xml", "/en/events/")
        if not petzi_urls:
            petzi_urls = get_petzi_event_urls()

        with app.app_context():
            known_urls = _get_known_urls()
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
            counts = {'created': 0, 'duplicate': 0, 'invalid': 0}
            for row in events:
                raw_styles = row.get('styles') or ''
                # petzi sitemap mixes concerts with theatre/workshop/club-night
                # rows; the raw 'concert' token is the only signal, so filter
                # on it before ingest's genre cleanup strips the token.
                if row.get('source') == 'petzi' and 'concert' not in raw_styles.lower():
                    continue
                # commit=False: batch commit below; in-batch url dupes are
                # still caught because ingest's dedup queries autoflush.
                result = ingest_event(row, commit=False)
                counts[result.status] += 1
                if result.status == 'invalid':
                    app.logger.warning(f"Scrape: dropped invalid row {row.get('url')}: {result.reason}")
            db.session.commit()
            app.logger.info(
                f"Scrape complete: {counts['created']} new, "
                f"{counts['duplicate']} duplicate, {counts['invalid']} invalid")
        set_last_scrape_time(datetime.now())
    except Exception:
        app.logger.exception("Scrape failed")
    finally:
        with _scrape_lock:
            _scrape_running = False


def _auto_scheduler(app):
    global _scrape_running
    while True:
        last = get_last_scrape_time()
        if last is None:
            wait = 0
        else:
            elapsed = (datetime.now() - last).total_seconds()
            wait = max(0, SCRAPE_INTERVAL_HOURS * 3600 - elapsed)
        if wait > 0:
            time_module.sleep(wait)
        with _scrape_lock:
            if _scrape_running:
                time_module.sleep(300)
                continue
            _scrape_running = True
        _scrape_and_import(app)
        time_module.sleep(SCRAPE_INTERVAL_HOURS * 3600)


def start_auto_scheduler(app):
    thread = threading.Thread(target=_auto_scheduler, args=(app,), daemon=True, name='scrape-scheduler')
    thread.start()
    return thread
