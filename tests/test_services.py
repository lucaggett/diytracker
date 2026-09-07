"""Service-layer tests: event hashing, venue get-or-create/dedup, scrape import."""

from datetime import date, datetime, time, timedelta

from diytracker.models import (
    Event,
    ScrapedEvent,
    SkippedUrl,
    Venue,
    VenueAccessibility,
    db,
    utcnow,
)
from diytracker.services.events import compute_event_hash
from diytracker.services.venue import (
    PLACEHOLDER_VENUE_NAME,
    find_dedup_candidates,
    get_or_create_venue,
    merge_group,
    normalize_city,
    normalize_name,
    resolve_existing_venue,
    select_survivor,
)


class TestComputeEventHash:
    def test_deterministic(self):
        args = ("Show", date(2026, 7, 1), time(20, 0), "Punk", "A,B", "", "15", 1)
        assert compute_event_hash(*args) == compute_event_hash(*args)

    def test_sensitive_to_each_field(self):
        base = ["Show", date(2026, 7, 1), time(20, 0), "Punk", "A,B", "", "15", 1]
        baseline = compute_event_hash(*base)
        # Changing any single component yields a different hash.
        for i, new in enumerate(
            ["Other", date(2026, 7, 2), time(21, 0), "Metal", "C", "link", "20", 2]
        ):
            variant = base.copy()
            variant[i] = new
            assert compute_event_hash(*variant) != baseline


class TestGetOrCreateVenue:
    def test_creates_when_absent(self, app):
        _venue, created = get_or_create_venue("New", "Addr", "Bern", "BE", "3000")
        db.session.commit()
        assert created is True
        assert Venue.query.count() == 1

    def test_returns_existing_match(self, app, make_venue):
        existing = make_venue(name="Hall", city="Bern", plz="3000")
        venue, created = get_or_create_venue("Hall", "Whatever", "Bern", "BE", "3000")
        assert created is False
        assert venue.id == existing.id
        assert Venue.query.count() == 1

    def test_distinguishes_by_city_and_plz(self, app, make_venue):
        make_venue(name="Hall", city="Bern", plz="3000")
        _, created = get_or_create_venue("Hall", "Addr", "Basel", "BS", "4000")
        assert created is True
        assert Venue.query.count() == 2


class TestResolveExistingVenue:
    """The gate in front of get_or_create_venue: exact-tuple matching is what
    minted a second venue for every scraped row that carried no postal code."""

    def test_blank_plz_still_finds_the_venue(self, app, make_venue):
        existing = make_venue(name="Kiff", city="Aarau", plz="5001", canton="AG")
        venue, candidates = resolve_existing_venue("KiFF", "Aarau", "")
        assert venue is existing
        assert candidates == []

    def test_casing_diacritics_and_trailing_space(self, app, make_venue):
        existing = make_venue(name="Sedel ", city="Emmenbrücke", plz="6020")
        venue, _ = resolve_existing_venue("sedel", "Emmenbrucke", "6020")
        assert venue is existing

    def test_city_punctuation_does_not_split_a_venue(self, app, make_venue):
        existing = make_venue(name="Grabenhalle", city="St-Gallen", plz="9000")
        venue, _ = resolve_existing_venue("Grabenhalle", "St. Gallen", "9000")
        assert venue is existing

    def test_conflicting_plz_is_ambiguous(self, app, make_venue):
        # metalgigs sends Gaswerk as 8401 where the venue is 8406. One of the
        # two is wrong and only a human knows which.
        make_venue(name="Gaswerk", city="Winterthur", plz="8406")
        venue, candidates = resolve_existing_venue("Gaswerk", "Winterthur", "8401")
        assert venue is None
        assert [v.name for v in candidates] == ["Gaswerk"]

    def test_two_confident_matches_are_ambiguous(self, app, make_venue):
        # The database already holds a duplicate pair: picking one at random
        # would pile a third row onto it.
        full = make_venue(name="Kiff", city="Aarau", plz="5001", address="Telli 118")
        thin = make_venue(name="KiFF", city="Aarau", plz="")
        venue, candidates = resolve_existing_venue("Kiff", "Aarau", "")
        assert venue is None
        # Most complete first, so the answer a merge would keep is preselected.
        assert [v.id for v in candidates] == [full.id, thin.id]

    def test_same_name_different_city_is_ambiguous(self, app, make_venue):
        make_venue(name="Sedel", city="Luzern", plz="6000")
        venue, candidates = resolve_existing_venue("Sedel", "Emmenbrücke", "")
        assert venue is None
        assert len(candidates) == 1

    def test_similar_name_in_the_same_city_is_ambiguous(self, app, make_venue):
        make_venue(name="X-tra Musikcafé", city="Zürich", plz="8005")
        venue, candidates = resolve_existing_venue("x-tra", "Zürich", "")
        assert venue is None
        assert len(candidates) == 1

    def test_same_address_in_the_same_city_is_ambiguous(self, app, make_venue):
        # Provitreff, Boschbar and Planet5 all sit at Sihlquai 240.
        make_venue(name="Provitreff", city="Zürich", plz="8005", address="Sihlquai 240")
        venue, candidates = resolve_existing_venue(
            "Planet5", "Zürich", "8005", "Sihlquai 240, 8005 Zürich"
        )
        assert venue is None
        assert len(candidates) == 1

    def test_genuinely_new_venue(self, app, make_venue):
        make_venue(name="Kiff", city="Aarau", plz="5001")
        assert resolve_existing_venue("Hirscheneck", "Basel", "4058") == (None, [])

    def test_blank_name_is_never_a_match(self, app, make_venue):
        make_venue(name="Kiff", city="Aarau", plz="5001")
        assert resolve_existing_venue("", "Aarau", "5001") == (None, [])


class TestScrapeImport:
    """Exercises the DB-import half of the scraper with canned scraped rows,
    so the real cleanup/dedup/filtering logic runs without any network."""

    def _run_import(self, app, monkeypatch, scraped_rows):
        from diytracker.services import scrape_events, scraper, venue_sources

        # _scrape_and_import() also walks the venue registry, and every one of
        # those fetchers goes to the live site. Emptying the registry keeps
        # this class offline; the venue fetchers have their own tests in
        # tests/test_venue_scrapers.py.
        monkeypatch.setattr(venue_sources, "get_sources", lambda: ())

        # The real scraper calls this once per source with a URL fragment;
        # return only the rows whose URL matches that fragment.
        monkeypatch.setattr(
            scrape_events,
            "get_sitemap_event_urls",
            lambda url, frag, **kw: [
                r["url"] for r in scraped_rows if frag in r["url"]
            ],
        )
        monkeypatch.setattr(scrape_events, "get_petzi_event_urls", list)
        url_to_row = {r["url"]: r for r in scraped_rows}
        monkeypatch.setattr(scrape_events, "parse_metalgigs_event", url_to_row.get)
        monkeypatch.setattr(scrape_events, "parse_petzi_event", url_to_row.get)
        monkeypatch.setattr(scraper, "set_last_scrape_time", lambda dt: None)

        scraper._scrape_and_import(app)

    def test_one_raising_parser_does_not_lose_the_other_rows(self, app, monkeypatch):
        """An unguarded parser unwound to the outer handler and took the
        whole run with it — including every listing source that had not run
        yet — because one event page had markup it didn't expect.
        """
        good = {
            "source": "metalgigs",
            "url": "https://metalgigs.ch/konzerte/good",
            "title": "Survivor",
            "city": "Basel",
            "start_date": "2026-08-01",
        }
        bad = {
            "source": "metalgigs",
            "url": "https://metalgigs.ch/konzerte/bad",
            "title": "Explodes",
            "city": "Basel",
            "start_date": "2026-08-02",
        }

        from diytracker.services import scrape_events, scraper, venue_sources

        monkeypatch.setattr(venue_sources, "get_sources", lambda: ())
        monkeypatch.setattr(
            scrape_events,
            "get_sitemap_event_urls",
            lambda url, frag, **kw: [r["url"] for r in (bad, good) if frag in r["url"]],
        )
        monkeypatch.setattr(scrape_events, "get_petzi_event_urls", list)

        def exploding_parser(url):
            if url == bad["url"]:
                raise ValueError("unexpected markup")
            return good

        monkeypatch.setattr(scrape_events, "parse_metalgigs_event", exploding_parser)
        monkeypatch.setattr(scrape_events, "parse_petzi_event", lambda url: None)
        monkeypatch.setattr(scraper, "set_last_scrape_time", lambda dt: None)

        counts = scraper._scrape_and_import(app)

        assert counts is not None  # the run completed
        assert ScrapedEvent.query.one().title == "Survivor"

    def test_one_raising_ingest_does_not_lose_the_batch(self, app, monkeypatch):
        """ingest_event() escaping skipped the batch commit, so every row
        staged before the bad one was discarded along with it.
        """
        rows = [
            {
                "source": "metalgigs",
                "url": f"https://metalgigs.ch/konzerte/{n}",
                "title": f"Gig {n}",
                "city": "Basel",
                "start_date": "2026-08-01",
            }
            for n in range(3)
        ]

        from diytracker.services import ingest, scraper

        real_ingest = ingest.ingest_event

        def flaky(row, **kw):
            if row["title"] == "Gig 1":
                raise RuntimeError("boom")
            return real_ingest(row, **kw)

        monkeypatch.setattr(scraper, "ingest_event", flaky)
        self._run_import(app, monkeypatch, rows)

        titles = sorted(r.title for r in ScrapedEvent.query.all())
        assert titles == ["Gig 0", "Gig 2"]

    def test_imports_and_cleans_scraped_rows(self, app, monkeypatch):
        rows = [
            {
                "source": "metalgigs",
                "url": "https://metalgigs.ch/konzerte/x",
                "title": "Gig",
                "styles": "Metal, Concert",  # 'Concert' is noise
                "region": "",
                "city": "Basel",
                "start_date": "2026-08-01",
                "ticket_url": "https://metalgigs.ch/tickets/kaufen",  # generic -> dropped
            }
        ]
        self._run_import(app, monkeypatch, rows)

        rec = ScrapedEvent.query.one()
        assert rec.styles == "Metal"  # noise stripped
        assert rec.region == "BS"  # inferred from city
        assert rec.ticket_url is None  # generic landing page dropped

    def test_dedupes_against_known_urls(self, app, monkeypatch):
        # A row whose URL already exists must not be re-imported.
        db.session.add(
            ScrapedEvent(source="metalgigs", url="https://metalgigs.ch/konzerte/dup")
        )
        db.session.commit()
        rows = [
            {
                "source": "metalgigs",
                "url": "https://metalgigs.ch/konzerte/dup",
                "title": "Dup",
                "styles": "Punk",
                "city": "Bern",
                "start_date": "2026-08-01",
            }
        ]
        self._run_import(app, monkeypatch, rows)
        assert ScrapedEvent.query.count() == 1

    def test_petzi_non_concert_rows_filtered_and_remembered(self, app, monkeypatch):
        rows = [
            {
                "source": "petzi",
                "url": "https://petzi.ch/en/events/theatre",
                "title": "A Play",
                "styles": "Theatre",  # no 'concert' token
                "city": "Bern",
                "start_date": "2026-08-01",
            }
        ]
        self._run_import(app, monkeypatch, rows)
        assert ScrapedEvent.query.count() == 0
        # The rejected URL is remembered so later runs don't re-fetch it.
        skipped = SkippedUrl.query.one()
        assert skipped.url == "https://petzi.ch/en/events/theatre"
        assert skipped.reason == "not a concert"

    def test_invalid_rows_remembered(self, app, monkeypatch):
        rows = [
            {
                "source": "metalgigs",
                "url": "https://metalgigs.ch/konzerte/broken",
                "title": "No Date",
                "styles": "Punk",
                # missing start_date -> ingest returns 'invalid'
            }
        ]
        self._run_import(app, monkeypatch, rows)
        assert ScrapedEvent.query.count() == 0
        skipped = SkippedUrl.query.one()
        assert skipped.url == "https://metalgigs.ch/konzerte/broken"
        assert skipped.reason.startswith("invalid:")

    def test_dedupes_against_skipped_urls(self, app, monkeypatch):
        # A previously rejected URL must not be fetched or parsed again.
        db.session.add(
            SkippedUrl(
                url="https://petzi.ch/en/events/theatre",
                source="petzi",
                reason="not a concert",
            )
        )
        db.session.commit()
        rows = [
            {
                "source": "petzi",
                "url": "https://petzi.ch/en/events/theatre",
                "title": "A Play",
                "styles": "Theatre",
                "city": "Bern",
                "start_date": "2026-08-01",
            }
        ]
        self._run_import(app, monkeypatch, rows)
        assert ScrapedEvent.query.count() == 0
        assert SkippedUrl.query.count() == 1  # no duplicate row added


# ── venue deduplication ───────────────────────────────────────────────────────


def _venue(vid, name, city, plz="", address="", coords="", canton="", token=None):
    """Unsaved Venue with an explicit id, for the pure matching functions."""
    venue = Venue(
        name=name,
        city=city,
        plz=plz,
        address=address,
        coords=coords,
        canton=canton,
        accessibility_token=token,
    )
    venue.id = vid
    return venue


class TestVenueNormalization:
    def test_normalize_name(self):
        assert normalize_name("  KiFF ") == "kiff"
        assert normalize_name("Case  à\tChocs") == "case a chocs"
        assert normalize_name(None) == ""

    def test_normalize_name_folds_diacritics(self):
        assert normalize_name("Bahnhöfli") == normalize_name("Bahnhofli")
        assert normalize_name("Château d'Erguël") == normalize_name("chateau d'erguel")
        assert normalize_name("Grosse Straße") == "grosse strasse"

    def test_normalize_city_strips_leading_plz(self):
        assert normalize_city("8005 Zürich") == "zurich"
        assert normalize_city(" Baden ") == "baden"
        # Only a *leading* PLZ is stripped — a trailing district number stays.
        assert normalize_city("Luzern 6") == "luzern 6"

    def test_normalize_city_strips_trailing_canton_code(self):
        assert normalize_city("Bremgarten AG") == "bremgarten"
        # A bare canton-code-like city name is left alone.
        assert normalize_city("AG") == "ag"


class TestFindDedupCandidates:
    def test_exact_name_same_city_is_auto(self):
        a, b = _venue(1, "Kiff", "Aarau"), _venue(2, "KiFF", "Aarau")
        auto, pairs = find_dedup_candidates([a, b])
        assert auto == [[a, b]]
        assert pairs == []

    def test_empty_city_joins_auto_group(self):
        a, b = _venue(1, "Fri-Son", "Fribourg"), _venue(2, "Fri-Son", "")
        auto, pairs = find_dedup_candidates([a, b])
        assert auto == [[a, b]]
        assert pairs == []

    def test_exact_name_different_city_is_interactive(self):
        a, b = _venue(1, "Sedel", "Emmenbrücke"), _venue(2, "Sedel", "Luzern 6")
        auto, pairs = find_dedup_candidates([a, b])
        assert auto == []
        assert [(p[0], p[1]) for p in pairs] == [(a, b)]

    def test_mixed_cities_yield_auto_subgroup_and_pairs(self):
        a = _venue(1, "Pont Rouge", "Montey")
        b = _venue(2, "Pont Rouge", "Monthey")
        c = _venue(3, "Pont Rouge", "Monthey")
        auto, pairs = find_dedup_candidates([a, b, c])
        assert auto == [[b, c]]
        assert {(p[0].id, p[1].id) for p in pairs} == {(1, 2), (1, 3)}

    def test_containment_same_city_is_interactive(self):
        a = _venue(1, "Royal", "Baden")
        b = _venue(2, "Kulturhaus Royal", " Baden")
        auto, pairs = find_dedup_candidates([a, b])
        assert auto == []
        assert [(p[0], p[1]) for p in pairs] == [(a, b)]

    def test_containment_different_city_ignored(self):
        a = _venue(1, "Gaswerk", "Winterthur")
        b = _venue(2, "Gaswerk Eventbar", "Seewen")
        assert find_dedup_candidates([a, b]) == ([], [])

    def test_similar_name_different_city_ignored(self):
        a = _venue(1, "Hirsche", "Basel")
        b = _venue(2, "Hirschen", "Sarnen")
        assert find_dedup_candidates([a, b]) == ([], [])

    def test_short_containment_ignored(self):
        a = _venue(1, "Rex", "Bern")
        b = _venue(2, "Rex Konzertlokal und Bar", "Bern")
        assert find_dedup_candidates([a, b]) == ([], [])

    def test_unrelated_same_city_names_ignored(self):
        a = _venue(1, "Dachstock", "Bern")
        b = _venue(2, "Rössli", "Bern")
        assert find_dedup_candidates([a, b]) == ([], [])

    def test_containment_across_diacritics(self):
        a = _venue(1, "Bahnhofli", "Biel/Bienne")
        b = _venue(2, "Bahnhöfli Biel", "Biel/Bienne")
        auto, pairs = find_dedup_candidates([a, b])
        assert auto == []
        assert [(p[0], p[1], p[2]) for p in pairs] == [
            (a, b, "one name contains the other")
        ]

    def test_exact_name_across_diacritics_different_city_is_interactive(self):
        a = _venue(1, "chateau d'erguël", "St. Imier")
        b = _venue(2, "Château d'Erguël", "Sonvilier")
        auto, pairs = find_dedup_candidates([a, b])
        assert auto == []
        assert [({p[0].id, p[1].id}, p[2]) for p in pairs] == [
            ({1, 2}, "same name, city differs")
        ]

    def test_same_address_same_city_is_interactive(self):
        a = _venue(1, "Komplex 457", "Zürich", address="Hohlstrasse 457")
        b = _venue(2, "Komplex Klub", "Zürich", address="Hohlstrasse 457, 8048 Zürich")
        auto, pairs = find_dedup_candidates([a, b])
        assert auto == []
        assert [(p[0], p[1], p[2]) for p in pairs] == [(a, b, "same address")]

    def test_trailing_canton_code_joins_city_bucket(self):
        a = _venue(1, "KUZEB - Kulturzentrum Bremgarten", "Bremgarten")
        b = _venue(2, "KUZEB", "Bremgarten AG")
        auto, pairs = find_dedup_candidates([a, b])
        assert auto == []
        assert [(p[0], p[1], p[2]) for p in pairs] == [
            (a, b, "one name contains the other")
        ]

    def test_empty_addresses_do_not_pair(self):
        a = _venue(1, "Dachstock", "Bern", address="")
        b = _venue(2, "Rössli", "Bern", address="")
        assert find_dedup_candidates([a, b]) == ([], [])


class TestSelectSurvivor:
    def test_most_complete_wins_despite_fewer_events(self):
        bare = _venue(1, "Kiff", "Aarau")
        full = _venue(2, "Kiff", "Aarau", plz="5001", address="Tellistrasse 118")
        survivor, losers = select_survivor([bare, full], {1: 10, 2: 1})
        assert survivor is full
        assert losers == [bare]

    def test_tie_broken_by_event_count_then_id(self):
        a = _venue(1, "X", "Bern", plz="3000")
        b = _venue(2, "X", "Bern", plz="3000")
        survivor, _ = select_survivor([a, b], {1: 1, 2: 5})
        assert survivor is b
        survivor, _ = select_survivor([a, b], {})
        assert survivor is a


class TestMergeGroup:
    def test_repoints_events_backfills_and_deletes(self, app, make_venue, make_event):
        survivor = make_venue(
            name="Kiff",
            city="Aarau",
            plz="5001",
            canton="AG",
            address="Tellistrasse 118",
        )
        loser = make_venue(name="KiFF", city="Aarau", plz="", canton="", coords="47,8")
        ev = make_event(venue=loser)
        stats = merge_group(survivor, [loser])
        db.session.commit()
        assert stats["events_repointed"] == 1
        assert db.session.get(Event, ev.id).venue_id == survivor.id
        assert Venue.query.count() == 1
        assert survivor.coords == "47,8"  # backfilled from the loser
        assert survivor.address == "Tellistrasse 118"  # not overwritten

    def test_merge_bumps_updated_at_for_sitemap(self, app, make_venue, make_event):
        survivor = make_venue(name="Kiff", city="Aarau")
        loser = make_venue(name="KiFF", city="Aarau")
        ev = make_event(venue=loser)
        stale = utcnow() - timedelta(days=30)
        survivor.updated_at = stale
        db.session.query(Event).filter_by(id=ev.id).update({"updated_at": stale})
        db.session.commit()
        merge_group(survivor, [loser])
        db.session.commit()
        # Bulk repoint bypasses the ORM onupdate; both must still be bumped
        # so the sitemap <lastmod> reflects the merge.
        assert db.session.get(Event, ev.id).updated_at > stale
        assert survivor.updated_at > stale

    def test_token_moves_when_survivor_has_none(self, app, make_venue):
        survivor = make_venue(name="Hall", city="Bern")
        loser = make_venue(name="Hall 2", city="Bern")
        token = loser.generate_accessibility_token()
        db.session.commit()
        merge_group(survivor, [loser])
        db.session.commit()
        assert survivor.accessibility_token == token
        assert Venue.query.count() == 1

    def test_survivor_token_kept_when_both_have_one(self, app, make_venue):
        survivor = make_venue(name="Hall", city="Bern")
        loser = make_venue(name="Hall 2", city="Bern")
        kept = survivor.generate_accessibility_token()
        loser.generate_accessibility_token()
        db.session.commit()
        stats = merge_group(survivor, [loser])
        db.session.commit()
        assert survivor.accessibility_token == kept
        assert any(f"#{loser.id}" in w for w in stats["warnings"])

    def test_accessibility_row_moves_to_survivor(self, app, make_venue):
        survivor = make_venue(name="Hall", city="Bern")
        loser = make_venue(name="Hall 2", city="Bern")
        acc = VenueAccessibility(venue_id=loser.id, updated_at=utcnow())
        db.session.add(acc)
        db.session.commit()
        merge_group(survivor, [loser])
        db.session.commit()
        assert acc.venue_id == survivor.id
        assert VenueAccessibility.query.count() == 1

    def test_accessibility_conflict_keeps_survivor_row(self, app, make_venue):
        survivor = make_venue(name="Hall", city="Bern")
        loser = make_venue(name="Hall 2", city="Bern")
        keep = VenueAccessibility(venue_id=survivor.id, updated_at=utcnow())
        drop = VenueAccessibility(venue_id=loser.id, updated_at=utcnow())
        db.session.add_all([keep, drop])
        db.session.commit()
        stats = merge_group(survivor, [loser])
        db.session.commit()
        rows = VenueAccessibility.query.all()
        assert [r.id for r in rows] == [keep.id]
        assert any("accessibility data" in w for w in stats["warnings"])


class TestArchiveDirectory:
    def test_past_boundary_is_start_of_today(self, app, make_event):
        from diytracker.services.archive import archive_directory

        make_event(name="Today Show", days_from_now=0)  # later today — not past
        past = make_event(name="Yesterday Show", days_from_now=-1)
        directory = archive_directory()
        months = dict(directory.get(past.date.year, []))
        assert months.get(past.date.month) == 1

    def test_cache_busts_on_event_write(self, app, make_event):
        from diytracker.services.archive import archive_directory
        from diytracker.services.cache import bust_cache

        make_event(days_from_now=-40)
        first = archive_directory()
        make_event(name="Another", days_from_now=-40, acts="X")
        bust_cache()
        second = archive_directory()
        assert sum(c for months in second.values() for _m, c in months) == 1 + sum(
            c for months in first.values() for _m, c in months
        )


class TestDbStats:
    def test_events_per_month_and_year(self, app, make_event):
        from diytracker.services import db_stats

        ev = make_event(days_from_now=-1)
        key = ev.date.strftime("%Y-%m")
        assert (key, 1) in db_stats.events_per_month()
        assert dict(db_stats.events_per_year())[ev.date.year] == 1
        series = db_stats.monthly_series(months=24)
        assert len(series) == 24
        assert {"month": key, "count": 1} in series

    def test_events_by_parent_genre_buckets(self, app, make_event):
        from diytracker.services import db_stats

        make_event(genre="Punk")
        make_event(genre="", acts="Mystery")  # untagged → Other
        counts = dict(db_stats.events_by_parent_genre())
        assert counts["Punk"] == 1
        assert counts["Other"] == 1

    def test_events_by_canton_resolves_and_buckets_unknown(
        self, app, make_event, make_venue
    ):
        from diytracker.services import db_stats

        make_event(venue=make_venue(name="ZH Hall", canton="ZH", city="Zürich"))
        make_event(
            venue=make_venue(
                name="Mystery Hall", canton="", city="Nowhereville", plz="0000"
            )
        )
        counts = dict(db_stats.events_by_canton())
        assert counts["Zürich"] == 1
        assert counts[None] == 1

    def test_top_venues_ranked(self, app, make_event, make_venue):
        from diytracker.services import db_stats

        busy = make_venue(name="Busy Hall")
        quiet = make_venue(name="Quiet Hall", plz="8002")
        make_event(venue=busy)
        make_event(venue=busy, acts="Other Band")
        make_event(venue=quiet)
        ranked = db_stats.top_venues()
        assert ranked[0][0].name == "Busy Hall"
        assert ranked[0][1] == 2

    def test_ticket_price_stats_mixed_inputs(self, app, make_event):
        from diytracker.services import db_stats

        for price, acts in [
            ("15.-", "a"),
            ("Kollekte", "b"),
            ("8-25", "c"),
            ("tba", "d"),
            ("", "e"),
        ]:
            make_event(ticket_price=price, acts=acts)
        stats = db_stats.ticket_price_stats()
        assert stats["total"] == 5
        assert stats["free"] == 1  # Kollekte
        assert stats["unparsed"] == 2  # tba, ""
        assert stats["paid"] == 2  # 15 and 8 (range lower bound)
        assert stats["min"] == 8.0
        assert stats["max"] == 15.0
        assert dict(stats["buckets"])["0–10"] == 1
        assert dict(stats["buckets"])["10–20"] == 1


class TestUpcomingFilter:
    """A multi-day event stays upcoming until its last day is over — the
    calendar always got this right, the other read paths did not."""

    def _running_festival(self, make_event):
        # Started yesterday, runs through tomorrow.
        ev = make_event(name="Long Weekender", days_from_now=-1)
        ev.end_date = (datetime.now() + timedelta(days=1)).date()
        ev.is_festival = True
        db.session.add(ev)
        db.session.commit()
        return ev

    def test_search_finds_a_running_festival(self, app, make_event):
        from diytracker.services.search import search_events

        self._running_festival(make_event)
        names = [e.name for e in search_events("weekender")]
        assert "Long Weekender" in names

    def test_canton_directory_keeps_a_running_festival(self, app, make_event):
        from diytracker.services.cantons import canton_directory

        ev = self._running_festival(make_event)
        directory = canton_directory()
        assert any(ev.venue_id in info["venue_ids"] for info in directory.values())

    def test_genre_directory_keeps_a_running_festival(self, app, make_event):
        from diytracker.services.genres import genre_directory

        self._running_festival(make_event)
        assert "punk" in genre_directory()

    def test_a_finished_event_is_not_upcoming(self, app, make_event):
        from diytracker.services.search import search_events

        make_event(name="Old Gig", days_from_now=-5)
        assert search_events("old gig") == []


class TestMergeDuplicateVenues:
    """scripts/merge_duplicate_venues repairs the rows the exact-tuple lookup
    left behind. The survivor is always the oldest, never the most complete."""

    def _pair(self, make_venue):
        # The shape the incident produced 22 times: an older complete row and a
        # newer one carrying nothing but the name and city.
        old = make_venue(name="Kiff", city="Aarau", plz="5001", address="Telli 118")
        new = make_venue(name="KiFF", city="Aarau", plz="")
        return old, new

    def test_dry_run_writes_nothing(self, app, make_venue, make_event):
        from scripts.merge_duplicate_venues import merge_duplicate_venues

        _old, new = self._pair(make_venue)
        make_event(venue=new, days_from_now=5)
        result = merge_duplicate_venues(dry_run=True)
        assert (result.groups, result.venues_deleted, result.events_repointed) == (
            1,
            1,
            1,
        )
        assert Venue.query.count() == 2
        assert db.session.get(Venue, new.id) is not None

    def test_keeps_the_older_row_and_repoints_its_events(
        self, app, make_venue, make_event
    ):
        from scripts.merge_duplicate_venues import merge_duplicate_venues

        old, new = self._pair(make_venue)
        old_id, new_id = old.id, new.id
        event = make_event(venue=new, days_from_now=5)
        merge_duplicate_venues(dry_run=False)
        assert Venue.query.count() == 1
        assert db.session.get(Venue, new_id) is None
        db.session.refresh(event)
        assert event.venue_id == old_id

    def test_is_idempotent(self, app, make_venue):
        from scripts.merge_duplicate_venues import merge_duplicate_venues

        self._pair(make_venue)
        merge_duplicate_venues(dry_run=False)
        again = merge_duplicate_venues(dry_run=False)
        assert again.groups == 0

    def test_backfills_what_the_survivor_is_missing(self, app, make_venue):
        from scripts.merge_duplicate_venues import merge_duplicate_venues

        # The one case where oldest and most-complete disagree: keep the older
        # row anyway, but take the fields it lacks off the one being deleted.
        old = make_venue(name="Erismannhof", city="", plz="", canton="")
        make_venue(name="Erismannhof", city="Zürich", plz="8004", canton="ZH")
        merge_duplicate_venues(dry_run=False)
        survivor = Venue.query.one()
        assert survivor.id == old.id
        assert (survivor.city, survivor.plz, survivor.canton) == (
            "Zürich",
            "8004",
            "ZH",
        )

    def test_suspects_are_reported_not_merged(self, app, make_venue):
        from scripts.merge_duplicate_venues import merge_duplicate_venues

        # Three different venues sharing one building — the case that makes
        # auto-merging on address wrong.
        make_venue(name="Provitreff", city="Zürich", plz="8005", address="Sihlquai 240")
        make_venue(name="Planet5", city="Zürich", plz="8005", address="Sihlquai 240")
        result = merge_duplicate_venues(dry_run=False)
        assert result.groups == 0
        assert Venue.query.count() == 2
        assert any("same address" in reason for _a, _b, reason in result.suspects)

    def test_interactive_merges_a_confirmed_suspect(self, app, make_venue, make_event):
        from scripts.merge_duplicate_venues import merge_duplicate_venues

        old = make_venue(name="Metbar", city="Lenzburg", plz="5600")
        new = make_venue(name="Met-Bar", city="Lenzburg", plz="5600")
        old_id, new_id = old.id, new.id
        event = make_event(venue=new, days_from_now=5)
        result = merge_duplicate_venues(dry_run=False, confirm=lambda *_: True)
        assert result.groups == 1
        assert Venue.query.count() == 1
        assert db.session.get(Venue, new_id) is None
        db.session.refresh(event)
        assert event.venue_id == old_id

    def test_interactive_declining_keeps_both(self, app, make_venue):
        from scripts.merge_duplicate_venues import merge_duplicate_venues

        make_venue(name="Metbar", city="Lenzburg", plz="5600")
        make_venue(name="Met-Bar", city="Lenzburg", plz="5600")
        result = merge_duplicate_venues(dry_run=False, confirm=lambda *_: False)
        assert result.groups == 0
        assert Venue.query.count() == 2
        assert len(result.declined) == 1
        assert result.suspects == []

    def test_interactive_quitting_keeps_the_rest(self, app, make_venue):
        from scripts.merge_duplicate_venues import merge_duplicate_venues

        make_venue(name="Metbar", city="Lenzburg", plz="5600")
        make_venue(name="Met-Bar", city="Lenzburg", plz="5600")
        make_venue(name="Schützi", city="Olten", plz="4600")
        make_venue(name="Schützi Olten", city="Olten", plz="4600")
        result = merge_duplicate_venues(dry_run=False, confirm=lambda *_: None)
        assert result.stopped_early is True
        assert result.groups == 0
        assert Venue.query.count() == 4
        assert len(result.suspects) == 2

    def test_interactive_always_keeps_the_older_row(self, app, make_venue):
        from scripts.merge_duplicate_venues import merge_duplicate_venues

        # The newer row is the more complete one; the older still survives.
        old = make_venue(name="AKUT", city="Thun", plz="")
        make_venue(name="AKuT Thun", city="Thun", plz="3600", address="Kurve 1")
        merge_duplicate_venues(dry_run=False, confirm=lambda *_: True)
        survivor = Venue.query.one()
        assert survivor.id == old.id
        assert (survivor.plz, survivor.address) == ("3600", "Kurve 1")

    def test_confirm_is_ignored_on_a_dry_run(self, app, make_venue):
        from scripts.merge_duplicate_venues import merge_duplicate_venues

        make_venue(name="Metbar", city="Lenzburg", plz="5600")
        make_venue(name="Met-Bar", city="Lenzburg", plz="5600")
        result = merge_duplicate_venues(dry_run=True, confirm=lambda *_: True)
        assert result.groups == 0
        assert Venue.query.count() == 2
        assert len(result.suspects) == 1


class TestPlaceholderVenue:
    def test_placeholder_is_never_auto_reused(self, app, make_venue):
        # The queue falls back to this name when a scraped row has no venue.
        # Two nameless rows are not the same place, and silently merging them
        # would hide unrelated shows behind one bucket venue.
        make_venue(name=PLACEHOLDER_VENUE_NAME, city="", plz="")
        venue, candidates = resolve_existing_venue(PLACEHOLDER_VENUE_NAME, "Bern", "")
        assert venue is None
        assert len(candidates) == 1


class TestNormaliseEventStatus:
    def test_maps_schema_org_urls(self):
        from diytracker.services.events import normalise_event_status

        assert (
            normalise_event_status("https://schema.org/EventCancelled") == "cancelled"
        )
        assert (
            normalise_event_status("https://schema.org/EventPostponed") == "postponed"
        )
        assert (
            normalise_event_status("https://schema.org/EventScheduled") == "scheduled"
        )

    def test_maps_bare_tokens_and_spellings(self):
        from diytracker.services.events import normalise_event_status

        assert normalise_event_status("EventCancelled") == "cancelled"
        assert normalise_event_status("cancelled") == "cancelled"
        assert normalise_event_status("Canceled") == "cancelled"

    def test_unknown_and_empty_default_to_scheduled(self):
        from diytracker.services.events import normalise_event_status

        assert normalise_event_status("") == "scheduled"
        assert normalise_event_status(None) == "scheduled"
        assert (
            normalise_event_status("https://schema.org/EventRescheduled") == "scheduled"
        )
