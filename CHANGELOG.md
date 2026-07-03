# Changelog

All notable changes to this project are documented in this file. The project
had no formal releases or tags before this changelog was written, so the
versions below were reconstructed from the git history and applied
retroactively. Versioning follows [Semantic Versioning](https://semver.org/):
`MAJOR.MINOR.PATCH`, with the project still in its `0.x` initial-development
phase (breaking changes can happen in a minor bump).

## [0.16.0] - 2026-07-03
- Added a public venue map at `/map`: all venues with coordinates are shown as markers on an OpenStreetMap base layer (Leaflet 1.9.4, vendored under `static/vendor/leaflet/` — no CDN), with popups showing address and upcoming-event count. Linked from the public nav and the sitemap; translated into de/fr/it/en.
- Added `scripts/geocode_venues.py` (+ a looser second pass in `scripts/geocode_venues_pass2.py`) to backfill `venue.coords` as `lat,lon` via the geo.admin.ch address search with OSM Nominatim as fallback. Ran it against the production database copy: 152 of 153 venues now carry coordinates (the "Unknown venue" placeholder intentionally has none).

## [0.15.0] - 2026-07-02
- Added `manage.py venue dedup` to merge duplicate venues. Exact name matches (case/whitespace variants) in the same city merge automatically; same-name/different-city and similar-name candidates are confirmed interactively. The most complete row survives, its missing fields are backfilled from the duplicates, and events plus accessibility data are repointed before deletion. Dry-run by default; `--apply` writes, `--db-path` targets an alternate database copy.

## [0.14.1] - 2026-07-02
- Added a share button to event cards.
- Public pages now carry OpenGraph/Twitter meta tags, so shared links render a preview card in WhatsApp and other messengers. Event pages use the flyer as the preview image where available, falling back to the site banner.

## [0.14.0] - 2026-07-02
- Restructured the repo: application code (`app.py`, `models.py`, `forms.py`, `utils.py`, `blueprints/`, `services/`) now lives in a `diytracker/` package. A thin root `app.py` shim keeps the `app:app` entry point working, so no gunicorn/systemd changes are needed.
- Added `diytracker/paths.py` as the single source of truth for repo-anchored paths; runtime behaviour (DB, logs, uploads, cache, translations) no longer depends on the process CWD.
- Removed the eight already-applied migration scripts (`migrations/completed_migrations/`); git history keeps them.
- Removed dead content globs from `tailwind.config.js`.

## [0.13.1] - 2026-07-02
`0a075f4..4e0f461`
- Synced `uv.lock` after the cleanup refactor.
- Updated README and scrubbed identifying information from public-facing documents.

## [0.13.0] - 2026-07-02
`31897eb..464638b`
- Big cleanup refactor: consolidated one-off scripts into `manage.py`, renamed `utils/` to `services/`, removed migration scripts already applied to production, added `.nvmrc` and CI polish.
- Bumped Tailwind and Node versions.

## [0.12.1] - 2026-07-02
`cf8bc33..eb330f6`
- Merged the unified-ingest feature branch and a dependabot PR into `main`; ruff reformat and merge cleanup.

## [0.12.0] - 2026-07-02
`0805c1b..bfef5ec`
- Added a unified multi-source event ingest system: new `ScrapedEvent` ingest columns, a shared ingest core used by all scrapers, and a token-authenticated `POST /api/ingest` push endpoint.
- Flyers carried through queue approval onto the final `Event`.
- Added an `eventbot` importer/forwarder and an ingest-focused test suite and docs.
- Formatting pass (ruff), GitHub Actions CI pipeline, sitemap, `.env.example`, dependency bump (Pillow), and missing templates.

## [0.11.1] - 2026-07-02
`630e3e9..d8c9a0e`
- Added deployment extras and a security audit script.
- Archived migration scripts already run against production.
- Fixed the package.json version number.

## [0.11.0] - 2026-07-01
`d20e12e`
- Security and correctness fixes from a full code review.

## [0.10.0] - 2026-06-29
`f498a97..719f972`
- Switched environment management from pip/requirements.txt to `uv`.
- Added an environment/user management admin interface.
- Added the project's first automated test suite.

## [0.9.2] - 2026-05-28 to 2026-06-02
`2e05574..4a9ffd5`
- Added "gabber" to the genre list.
- Added a README project overview.

## [0.9.1] - 2026-05-16
`eea271c..e962183`
- Added account-request info to the login page, a delete button, and admin event sorting by date.
- Fixed flash-message placeholder rendering.

## [0.9.0] - 2026-05-06 to 2026-05-08
`ca8b54e..76a851d`
- Performance pass: server-side gzip compression, CSS minification, and calendar caching.
- Added visit analytics with crawler filtering.
- Visual refresh: new frontpage style, header banner, torn-paper graphic, PNG export by genre category.
- General cleanup and refactor of the codebase.

## [0.8.1] - 2026-04-29
`75c6e4f..fa368f5`
- Added card animations and moved the language selector to the footer.

## [0.8.0] - 2026-04-21 to 2026-04-23
`7319396..30f2579`
- Full frontend rewrite (three-part rollout).
- Complete internationalization: Italian, French, German and English translations for the site and calendar export.
- Venue editing in the admin interface, venue accessibility info, and a traffic-report view.

## [0.7.0] - 2026-04-01 to 2026-04-09
`bbab306..2baa973`
- Added a Canva-ready image export with a black background layer.
- Desktop UI rework and further queue-management improvements.
- Replaced manually-triggered scraping with a background job, tightened to run every hour.

## [0.6.0] - 2026-03-17
`553e82c..398103c`
- Admin utility features and general file cleanup.
- Event deduplication to prevent redundant scraping.
- Added weekly calendar export.

## [0.5.0] - 2026-03-12 to 2026-03-13
`6fa80f1..6822102`
- Reworked the review queue: proper editing, user removal/regeneration fixes.
- Replaced magic-string auth with real login/cookie sessions, plus self-service password reset.
- Mobile frontend improvements and canton name parsing/normalization.

## [0.4.0] - 2025-11-03
`91ec07b..f7732c9`
- Added automated event scraping (metalgigs, petzi) behind a review queue.
- Updated the database schema to support scraped events.

## [0.3.1] - 2025-03-10
`9262ba9..80f1c74`
- Reworked submission handling and fixed the event-editing form and email links.

## [0.3.0] - 2024-09-30 to 2024-10-04
`e8ebe70..50cba9a`
- Reworked the submission form and split venues into their own table.
- Added favicon and SEO meta tags.

## [0.2.0] - 2024-09-22 to 2024-09-24
`629126d..c230ce4`
- Modals and extended database models.
- Full admin interface implementation.
- Mobile-friendly redesign with event filtering.
- Security improvements and better logging.

## [0.1.0] - 2024-09-08 to 2024-09-11
`91c0939..5b90b60`
- Initial Flask application scaffold with basic event listing and sorting.
