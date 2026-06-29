"""Service-layer tests: event hashing, venue get-or-create, scrape import."""
from datetime import date, time

from models import db, Event, ScrapedEvent, Venue
from services.events import compute_event_hash
from services.venue import get_or_create_venue


class TestComputeEventHash:
    def test_deterministic(self):
        args = ('Show', date(2026, 7, 1), time(20, 0), 'Punk', 'A,B', '', '15', 1)
        assert compute_event_hash(*args) == compute_event_hash(*args)

    def test_sensitive_to_each_field(self):
        base = ['Show', date(2026, 7, 1), time(20, 0), 'Punk', 'A,B', '', '15', 1]
        baseline = compute_event_hash(*base)
        # Changing any single component yields a different hash.
        for i, new in enumerate(['Other', date(2026, 7, 2), time(21, 0),
                                  'Metal', 'C', 'link', '20', 2]):
            variant = base.copy()
            variant[i] = new
            assert compute_event_hash(*variant) != baseline


class TestGetOrCreateVenue:
    def test_creates_when_absent(self, app):
        venue, created = get_or_create_venue('New', 'Addr', 'Bern', 'BE', '3000')
        db.session.commit()
        assert created is True
        assert Venue.query.count() == 1

    def test_returns_existing_match(self, app, make_venue):
        existing = make_venue(name='Hall', city='Bern', plz='3000')
        venue, created = get_or_create_venue('Hall', 'Whatever', 'Bern', 'BE', '3000')
        assert created is False
        assert venue.id == existing.id
        assert Venue.query.count() == 1

    def test_distinguishes_by_city_and_plz(self, app, make_venue):
        make_venue(name='Hall', city='Bern', plz='3000')
        _, created = get_or_create_venue('Hall', 'Addr', 'Basel', 'BS', '4000')
        assert created is True
        assert Venue.query.count() == 2


class TestScrapeImport:
    """Exercises the DB-import half of the scraper with canned scraped rows,
    so the real cleanup/dedup/filtering logic runs without any network."""

    def _run_import(self, app, monkeypatch, scraped_rows):
        import services.scraper as scraper

        fake = type('FakeScraper', (), {})()
        # The real scraper calls this once per source with a URL fragment;
        # return only the rows whose URL matches that fragment.
        fake.get_sitemap_event_urls = lambda url, frag: [
            r['url'] for r in scraped_rows if frag in r['url']
        ]
        fake.get_petzi_event_urls = lambda: []
        url_to_row = {r['url']: r for r in scraped_rows}
        fake.parse_metalgigs_event = lambda url: url_to_row.get(url)
        fake.parse_petzi_event = lambda url: url_to_row.get(url)
        monkeypatch.setattr(scraper, '_load_scraper', lambda: fake)
        monkeypatch.setattr(scraper, 'set_last_scrape_time', lambda dt: None)

        scraper._scrape_and_import(app)

    def test_imports_and_cleans_scraped_rows(self, app, monkeypatch):
        rows = [{
            'source': 'metalgigs', 'url': 'https://metalgigs.ch/konzerte/x',
            'title': 'Gig', 'styles': 'Metal, Concert',  # 'Concert' is noise
            'region': '', 'city': 'Basel', 'start_date': '2026-08-01',
            'ticket_url': 'https://metalgigs.ch/tickets/kaufen',  # generic -> dropped
        }]
        self._run_import(app, monkeypatch, rows)

        rec = ScrapedEvent.query.one()
        assert rec.styles == 'Metal'                # noise stripped
        assert rec.region == 'BS'                   # inferred from city
        assert rec.ticket_url is None               # generic landing page dropped

    def test_dedupes_against_known_urls(self, app, monkeypatch):
        # A row whose URL already exists must not be re-imported.
        db.session.add(ScrapedEvent(source='metalgigs',
                                    url='https://metalgigs.ch/konzerte/dup'))
        db.session.commit()
        rows = [{
            'source': 'metalgigs', 'url': 'https://metalgigs.ch/konzerte/dup',
            'title': 'Dup', 'styles': 'Punk', 'city': 'Bern',
            'start_date': '2026-08-01',
        }]
        self._run_import(app, monkeypatch, rows)
        assert ScrapedEvent.query.count() == 1

    def test_petzi_non_concert_rows_filtered(self, app, monkeypatch):
        rows = [{
            'source': 'petzi', 'url': 'https://petzi.ch/en/events/theatre',
            'title': 'A Play', 'styles': 'Theatre',  # no 'concert' token
            'city': 'Bern', 'start_date': '2026-08-01',
        }]
        self._run_import(app, monkeypatch, rows)
        assert ScrapedEvent.query.count() == 0
