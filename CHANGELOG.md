# Changelog

All notable changes to this project are documented in this file. The project
had no formal releases or tags before this changelog was written, so the
versions below were reconstructed from the git history and applied
retroactively. Versioning follows [Semantic Versioning](https://semver.org/):
`MAJOR.MINOR.PATCH`, with the project still in its `0.x` initial-development
phase (breaking changes can happen in a minor bump).

## [0.22.0] - 2026-07-10
- Big SEO pass, part 1 (structured data, canonicals, city pages, sitemap). The hand-built JSON-LD in the event and venue templates moved into a new `diytracker/services/seo.py` with proper serialization: `MusicEvent` now gets a timezone-aware `startDate` (Europe/Zurich), a real `eventStatus`, a `performer` list parsed from the acts field, geo coordinates, and an `offers` block with a *numeric* price parsed from the free-text ticket field ("Kollekte"/"gratis" → price 0, unparseable prices omit the block instead of emitting invalid junk like "31.30.-"). Venue pages gain `amenityFeature` entries built from the accessibility questionnaire (step-free entrance, accessible toilet, hearing loop, …), and the homepage gets `WebSite` JSON-LD.
- Every public page now has a `<link rel="canonical">` (and `og:url` uses it instead of leaking whatever host/scheme the request arrived on). The canonical origin comes from a new `CANONICAL_HOST` env var — set `CANONICAL_HOST=https://diytracker.ch` in the production `.env`; without it, dev/tests fall back to the request host.
- New city landing pages at `/<city-slug>/` (e.g. `/zuerich/`, `/geneve/`) targeting the "konzerte zürich"-style query space: H1, translatable intro, server-rendered lists of upcoming events and venues. Cities are derived from the venues table (no hardcoding) by grouping the free-text city values by slug — "GENÈVE" and "Genève" merge into one page — with Swiss-style slugs (ü→ue, é→e) from a new `slugify()`. Only cities with at least one upcoming event resolve; a custom URL converter excludes reserved path segments at routing time so `/logout` etc. behave exactly as before. The footer links the top 10 cities by upcoming-event count.
- Events can now be marked cancelled/postponed: new `event.status` column plus a status dropdown on the admin edit form, feeding both the JSON-LD `eventStatus` and a visible banner on the event page. Past event pages (which have always stayed live) now show a "This event has already taken place." notice with links to upcoming shows at the same venue and the city page.
- Sitemap improvements: honest `<lastmod>` from new `updated_at` columns on events and venues (auto-touched on every edit), city landing pages included, events older than two years aged out (the pages themselves stay live), and the placeholder `/de/impressum` etc. "WIP" routes dropped until they have real content. `robots.txt` and the sitemap are now exempt from scrape detection scoring, and hits from search-engine UAs (Googlebot, bingbot, …) are logged for crawl auditing. Run `migrations/migrate_add_seo_columns.py` on the server before restarting.
- Added a reference nginx config (`deploy/nginx-diytracker.conf`, live copy on the box is untracked): 301s from `www.diytracker.ch` and plain HTTP to `https://diytracker.ch` (so canonicals, `og:url` and the sitemap agree on the apex host), static files from disk with 1-year cache headers, and an app-down error page — `error_page 502 503 504` now serves `static/errors/offline.html`, a self-contained zine-styled "OFFLINE" page (inline copies of the flyer-card/stamp/mis-reg styles, fonts from `/static/`, auto-retry every 30 s) instead of nginx's default white error when gunicorn is unreachable. App-rendered 404/500 pages are unaffected. Setup notes in `deploy/README.md`.
- Deferred to a follow-up: hreflang/language-prefixed URLs, reverse-DNS crawler verification, genre sub-pages, and a Lighthouse pass.

## [0.21.2-4] - 2026-07-10
- Added inbound scraping detection: a `before_request` hook (`diytracker/services/scrape_detection.py`) scores anonymous traffic per IP over a 10-minute sliding window against signals that also catch common evasion tricks — a honeypot page linked invisibly in the footer and disallowed in `robots.txt` (which now exists, served by Flask, honeypot and admin paths disallowed, sitemap referenced); known HTTP-library/headless user agents and missing UAs; *spoofed* browser UAs (claims a browser but sends no `Accept-Language`, or claims modern Chrome without `sec-ch-ua` client hints); sustained request volume; metronomic inter-request timing that survives randomised sleep jitter; sequential `/events/<id>` enumeration; deep crawls with no same-origin navigation; and the same header fingerprint arriving from many IPs at once (rotating proxies — only counted alongside another signal, since stock browsers share fingerprints). IPs crossing the threshold are persisted to a new `scrape_suspect` table (auto-created by `db.create_all()`, writes throttled to once a minute per IP) and listed with their score, signal badges, UA and sample paths on a new admin page at `/admin/scrape-suspects` ("Suspects" in the admin nav), with a clear-list button. Detection only — nothing is blocked. State is in-memory per gunicorn worker (same trade-off as flask-limiter), so volume thresholds are effectively up to 4× with 4 workers.
- Made the scraper a much politer citizen, cutting its load on petzi.ch by ~99%. The prod log showed ~6,300 requests/day re-fetching the same ~342 petzi pages every hour: rows rejected as non-concerts (or invalid) were never recorded, so every run re-downloaded them. Rejected URLs now land in a new `skipped_url` table (auto-created by `db.create_all()`) that feeds the known-URL dedup set. Also: sitemap-index scraping now takes hints to skip irrelevant sub-sitemaps (metalgigs' 2.1 MB bands and 180 KB venues sitemaps are no longer downloaded hourly), the scrape interval went from 1 to 6 hours, the scraper honors each host's robots.txt (parsers cached per host for a day), identifies itself with an honest `diytracker (+https://diytracker.ch; …)` User-Agent instead of rotating fake browser strings, and no longer retries on 403 (a "go away" isn't a transient error; 429 with Retry-After is still retried).
- Fixed the map's X markers dwarfing the country on phones: the marks are fixed-pixel icons, so at the zoomed-out level a portrait screen needs to fit Switzerland they covered half the map. They now scale with the zoom level (down to half size, never above their current size, driven by a single CSS variable so no icons are regenerated); the invisible tap target keeps its full 30px. The map card also drops its 78vh height on small screens for a 10:7 aspect ratio matching Switzerland's shape, removing the empty paper bands above and below the country.
- Added one-command remote deploys: `scripts/deploy_remote.sh` ssh-es into the production box (host from `DIYTRACKER_DEPLOY_HOST` or the first argument) and runs the new server-side `deploy/deploy.sh`, which does `git pull --ff-only`, `uv sync`, restarts `diytracker.service`, and fails loudly if the service doesn't come back up. The restart no longer needs the root password: a new sudoers drop-in (`deploy/sudoers-diytracker`, installed once to `/etc/sudoers.d/`) lets `diytrackeruser` run exactly `systemctl restart diytracker.service` passwordless. Setup and usage documented in `deploy/README.md`.
- Added cache busting to the deploy script
- Removed Impressum, AGB, etc since they were mostly fodder for scrapers to get my personal info and their legal necessity is questionable.


## [0.21.1] - 2026-07-09
- Added an i18n status check to CI (`scripts/i18n_status.py`): extracts a fresh string catalog from the source tree, verifies `translations/messages.pot` is in sync, and reports per-locale coverage (translated/untranslated/fuzzy/missing/obsolete) as a table in the job summary. The job fails on any stale catalog or any untranslated, fuzzy, or missing string; obsolete entries are reported but don't fail. Runnable locally with `uv run python scripts/i18n_status.py`.
- Fixed the 19 fuzzy-matched translations per locale that the new check flagged: the venue-management strings had been auto-populated with wrong nearby matches (e.g. "Edit Venue" showed "EVENT BEARBEITEN"/"MODIFIER ÉVÉNEMENT", "back to venues" pointed to the calendar, and the English catalog mapped "Loud" to "Logout" and "Calendar" to "Back to calendar" in the public header). All four locales are now at 100% with correct strings and recompiled `.mo` catalogs.
- Removed the map's always-visible gesture note in the corner; the transient paper-scrap hint that flashes on a wrong gesture (and the zoom buttons) remain.

## [0.21.0] - 2026-07-09
- Replaced the map's "Show all venues" button with a fixed-size toggle switch (44×24, ink-bordered with a tilted square knob to fit the zine look): only the knob position and track colour change on toggle, and the venue counter is now a single static "X of Y venues with upcoming shows" line, so the row no longer reflows on mobile when toggled. The switch is keyboard-operable and exposed as `role="switch"` to assistive tech.
- Compacted the expanded event cards on the calendar: the eight stacked LABEL/value rows are now three to four icon-led lines (hand-sketched clock/pin/tag/wheelchair symbols shared via `svg_defs.html`), the raw ticket URL became a "Tickets · price" button, and the venue name and city/canton rows were dropped from the detail view since the collapsed header already shows them. Roughly half the previous height per card; screen readers still get the old field labels via visually-hidden text. The share button is unchanged.
- The map no longer traps page scrolling: one-finger drag scrolls the page (two fingers pan, pinch zooms) on touch devices, and the mouse wheel scrolls the page (Ctrl/⌘+wheel zooms) on desktop — the Google Maps embed pattern. A small persistent corner note states the right gesture per device, and a paper-scrap hint flashes when the wrong gesture is used. Zoom buttons still work everywhere. Implemented in ~40 lines instead of vendoring the unmaintained leaflet-gesture-handling plugin.

## [0.20.0]
- The calendar's canton filter now always lists all 26 cantons with their upcoming-event count in brackets (e.g. "Bern (12)"); cantons without events are greyed out and unselectable instead of missing from the list. Liechtenstein still only appears when it actually has events. Canton values in the filter are now normalised to canton codes server-side, so a venue stored as "Neuchâtel" counts under Neuenburg instead of appearing as a separate option.
- Reworked the map's hand-drawn X markers again: instead of cycling four pre-baked stroke shapes (whose curvature was too subtle to read at marker size), every mark is now generated from a per-venue seed — strokes bow visibly off the diagonal, overshoot the corners unevenly, get retraced with a thinner second pen pass, and each mark tilts differently. Seeded with mulberry32 so marks are unique per venue but stable across renders. Also fixed the hover zoom, which the old inline rotation style had been overriding.

## [0.19.2] - 2026-07-03
- CI now auto-formats with `ruff format` and pushes the fix back to the branch (same-repo pushes/PRs only; forks fall back to a failing check) instead of just failing on unformatted code; `ruff check` still fails the build on real lint errors.
- Added test coverage reporting: `pytest-cov` runs in CI and uploads results to Codecov.
- Added CI/coverage/Python-version/last-commit badges to the README.

## [0.19.1] - 2026-07-03
- Redrawn the map's X markers as hand-drawn pen strokes: four looser stroke shapes (curved, uneven, with round caps and slightly varying weights) cycle across venues alongside the existing rotation scatter, replacing the ruler-straight lines that made the marks look tilted rather than hand-made. The red-over-blue misprint and the faded grey no-upcoming variant are unchanged.

## [0.19.0] - 2026-07-03
- Added public venue pages at `/venues/<id>/`: venue name and address, a small non-interactive Switzerland-cutout locator map, the accessibility-info link where available, and a list of all upcoming events at the venue linking to their event pages. Listed in the sitemap for every venue; translated into de/fr/it/en.
- The map now loads all venues (not just those with upcoming shows): venues without upcoming events render as faded ink-grey X stamps in a separate layer, toggled by a new "Show all venues" button below the map, with the venue-count caption switching accordingly.
- Map popups now link to the venue's page, and venues without upcoming shows say so in the popup instead of showing a count.

## [0.18.0] - 2026-07-03
- Reworked the map page into a "cutout" of Switzerland: an inverted mask built from a simplified OSM border polygon (vendored under `static/geo/switzerland.geojson`) hides all tiles outside the country, the border is drawn in ink, and panning/zooming is locked to Switzerland. Venues outside the Swiss bounding box are filtered out server-side since they would be unreachable.
- Made the map page more mobile-friendly: removed the page title so the map sits at the top, made the map taller (`78dvh`), tightened mobile padding, and enlarged the X markers to 30px tap targets on touch screens.

## [0.17.0] - 2026-07-03
- The venue map now only shows venues with events in the next three months (the calendar's horizon), instead of every venue with coordinates.
- Restyled the map to match the xerox-zine look: tiles are desaturated and multiply-blended onto the paper background, markers are hand-stamped red X marks with the site's blue mis-registration ghost, and popups, zoom buttons and attribution are ink-bordered paper with hard offset shadows.

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
