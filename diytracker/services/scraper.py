import os
import threading
import time as time_module
from datetime import datetime

from diytracker.paths import INSTANCE_DIR
from diytracker.models import db, Event, ScrapedEvent, SkippedUrl
from diytracker.services.ingest import ingest_event

LAST_SCRAPE_FILE = str(INSTANCE_DIR / "last_scrape.txt")
# Event listings change a few times a day at most; scraping more often
# than this just re-downloads unchanged sitemaps and pages.
SCRAPE_INTERVAL_HOURS = 6

_scrape_lock = threading.Lock()
_scrape_running = False
_scrape_progress = {"total": 0, "processed": 0, "started_at": None, "phase": ""}


def get_last_scrape_time():
    try:
        with open(LAST_SCRAPE_FILE) as f:
            return datetime.fromisoformat(f.read().strip())
    except (FileNotFoundError, ValueError):
        return None


def set_last_scrape_time(dt):
    os.makedirs(os.path.dirname(LAST_SCRAPE_FILE), exist_ok=True)
    with open(LAST_SCRAPE_FILE, "w") as f:
        f.write(dt.isoformat())


def is_running():
    return _scrape_running


def get_progress():
    return dict(_scrape_progress)


def _get_known_urls():
    scraped_urls = {
        r.url for r in ScrapedEvent.query.with_entities(ScrapedEvent.url).all() if r.url
    }
    event_urls = {
        r.source_url
        for r in Event.query.with_entities(Event.source_url)
        .filter(Event.source_url.isnot(None))
        .all()
    }
    skipped_urls = {r.url for r in SkippedUrl.query.with_entities(SkippedUrl.url).all()}
    return scraped_urls | event_urls | skipped_urls


def _record_skipped(url, source, reason):
    """Remember a rejected URL so later runs don't re-fetch it."""
    if not url:
        return
    db.session.add(
        SkippedUrl(url=url[:300], source=source, reason=(reason or "")[:200] or None)
    )


def _scrape_and_import(app):
    """Run one scrape+import. Returns the ingest counts, or None if the run
    failed (the exception is logged, never raised — the scheduler thread must
    survive a bad run)."""
    global _scrape_running, _scrape_progress
    counts = None
    try:
        from diytracker.services.scrape_events import (
            configure_logging,
            get_petzi_event_urls,
            get_sitemap_event_urls,
            parse_metalgigs_event,
            parse_petzi_event,
        )

        # Handlers attach lazily so merely importing the parsers (as the web
        # workers do) never opens logs/scrape_events.log; an actual scrape
        # run should still be captured there.
        configure_logging()

        _scrape_progress = {
            "total": 0,
            "processed": 0,
            "started_at": datetime.now().isoformat(),
            "phase": "Fetching sitemaps",
        }

        mg_urls = get_sitemap_event_urls(
            "https://metalgigs.ch/sitemap.xml",
            "/konzerte/",
            sub_sitemap_hints=["concert"],
        )
        petzi_urls = get_sitemap_event_urls(
            "https://www.petzi.ch/en/sitemap.xml", "/en/events/"
        )
        if not petzi_urls:
            petzi_urls = get_petzi_event_urls()

        with app.app_context():
            known_urls = _get_known_urls()
        all_urls = [
            ("metalgigs", url, parse_metalgigs_event)
            for url in mg_urls
            if url not in known_urls
        ] + [
            ("petzi", url, parse_petzi_event)
            for url in petzi_urls
            if url not in known_urls
        ]

        app.logger.info(
            f"Scrape: {len(mg_urls) + len(petzi_urls)} total URLs, {len(all_urls)} new after dedup"
        )
        _scrape_progress["total"] = len(all_urls)
        _scrape_progress["phase"] = "Scraping events"

        events = []
        for _source, url, parser in all_urls:
            parsed = parser(url)
            if parsed:
                events.append(parsed)
            _scrape_progress["processed"] += 1

        _scrape_progress["phase"] = "Importing to database"
        with app.app_context():
            counts = {"created": 0, "duplicate": 0, "invalid": 0, "skipped": 0}
            for row in events:
                raw_styles = row.get("styles") or ""
                # petzi sitemap mixes concerts with theatre/workshop/club-night
                # rows; the raw 'concert' token is the only signal, so filter
                # on it before ingest's genre cleanup strips the token.
                if row.get("source") == "petzi" and "concert" not in raw_styles.lower():
                    _record_skipped(row.get("url"), "petzi", "not a concert")
                    counts["skipped"] += 1
                    continue
                # commit=False: batch commit below; in-batch url dupes are
                # still caught because ingest's dedup queries autoflush.
                result = ingest_event(row, commit=False)
                counts[result.status] += 1
                if result.status == "invalid":
                    _record_skipped(
                        row.get("url"), row.get("source"), f"invalid: {result.reason}"
                    )
                    app.logger.warning(
                        f"Scrape: dropped invalid row {row.get('url')}: {result.reason}"
                    )
            db.session.commit()
            app.logger.info(
                f"Scrape complete: {counts['created']} new, "
                f"{counts['duplicate']} duplicate, {counts['invalid']} invalid, "
                f"{counts['skipped']} skipped (non-concert)"
            )
        set_last_scrape_time(datetime.now())
    except Exception:
        app.logger.exception("Scrape failed")
        counts = None
    finally:
        with _scrape_lock:
            _scrape_running = False
    return counts


def run_scrape_now(app):
    """Run one scrape synchronously, on demand (the admin tool's trigger).

    Returns (started, counts): started is False when this process is already
    scraping, counts is None when the run itself failed.

    The lock is a module global, so it only knows about *this* process. The
    admin tool runs in its own interpreter and therefore cannot see a scrape
    already running inside a gunicorn worker; the two can overlap. That costs
    duplicate fetching, not duplicate data — ingest dedups on URL and the
    known-URL set is read at the start of each run.
    """
    global _scrape_running
    with _scrape_lock:
        if _scrape_running:
            return False, None
        _scrape_running = True
    try:
        return True, _scrape_and_import(app)
    finally:
        # _scrape_and_import clears the flag itself on every path it controls;
        # this covers the one it doesn't — blowing up before its own try —
        # which would otherwise leave the process permanently "busy" and the
        # trigger dead until restart.
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
    thread = threading.Thread(
        target=_auto_scheduler, args=(app,), daemon=True, name="scrape-scheduler"
    )
    thread.start()
    return thread
