# Changelog

All notable changes to this project are documented in this file. The project
had no formal releases or tags before this changelog was written, so the
versions below were reconstructed from the git history and applied
retroactively. Versioning follows [Semantic Versioning](https://semver.org/):
`MAJOR.MINOR.PATCH`, with the project still in its `0.x` initial-development
phase (breaking changes can happen in a minor bump).

## [0.37.0] - 2026-07-16
- Canton (`/<canton>/`), genre (`/genre/<slug>/`) and label (`/label/<slug>/`) pages now look like the main calendar: the same expandable date-cards (flyer, doors, address, accessibility link, tickets/share buttons) instead of flat link lists. The card markup and its toggle/share JS were extracted into shared partials (`_partials/event_cards.html`, `_partials/event_cards_js.html`) used by all four pages, ending the calendar/label-page duplication. Venue (and genre-page canton) link lists stay below the cards; no filter modal on the sub-pages.
- On those three pages the site banner is replaced by a hero on the same dark zone: a "← Back to calendar" link, an eyebrow (Canton/Genre/Label), and the page name in large display type — with the label's logo for label pages. When a description exists the hero switches to a compact-title layout with the blurb underneath (canton/genre reuse their existing translated blurbs). The torn-edge divider and nav bar stay; the banner preload is skipped on hero pages. The old `_partials/header_public.html` was split into `header_banner.html` + `header_nav.html` with a `header_hero` block in `base_public.html`.
- New `label.description` column (plain text, max 1000 chars), editable in the promoter label form and shown on the public label page's hero. **Deploy: run `migrations/migrate_add_label_description.py` on the server** (idempotent; fresh DBs get the column via `db.create_all()`).
- Translation catalogs back at 100%: the new label-description hint plus four strings from the 0.35.1 missing-venue panel that had never been translated (DE/FR/IT/EN).

## [0.36.1] - 2026-07-16
- The admin statistics page is more minimal: dropped the explanatory footnotes under "Most viewed events" (view-counting methodology) and "Ticket prices" (price-parsing caveat). Translation catalogs re-extracted (two strings removed).

## [0.36.0] - 2026-07-16
- New `scripts/recover_db.sh`: rebuilds a corrupted SQLite database in place via `sqlite3 .recover` (written after prod's `events.db` developed a b-tree with out-of-order rowids, making venue #108 invisible to primary-key lookups — edit-venue 404'd and event assignment failed while the venue still appeared in list views). The script refuses to touch a healthy database (`--force` overrides), stops/starts `diytracker.service` around the swap (`--no-service` for local copies), keeps the corrupt file and its `-wal`/`-shm` siblings as timestamped `.corrupt.*` backups, and only swaps the rebuilt file in after it passes `PRAGMA integrity_check`, printing before/after row counts and any pre-existing dangling foreign-key references. Because pre-3.40 sqlite3's `.recover` fails on this corruption class with "SQL logic error" (as prod's did), the script falls back to a table-copy salvage — recreate the schema, `INSERT OR IGNORE ... SELECT DISTINCT` each table via full scans (which still see every row on this corruption; the PK dedupes double-scanned rows), then recreate indexes/triggers — forceable via `--salvage`; both paths produce byte-identical table contents on the prod corruption.

## [0.35.1] - 2026-07-15
- Hotfix: `/admin/edit_event/<id>` 500'd (`AttributeError: 'NoneType' object has no attribute 'name'`) for events whose `venue_id` points at a venue that no longer exists — SQLite doesn't enforce the FK here, so a dangling reference can slip through. The edit page now detects this, flashes a message asking the admin to re-pick a venue, and leaves the venue fields blank instead of crashing.
- The admin dashboard now surfaces these events proactively: a "Events with missing venue" panel (hidden when empty) lists every event whose venue was deleted out from under it, each with a direct link to the edit page to fix it — no more finding out via a crash report.

## [0.35.0] - 2026-07-15
- Impressum and Datenschutzerklärung are live again (minimal versions) at `/<lang>/impressum` and `/<lang>/datenschutz` — previously placeholder routes returning "WIP". Impressum now lists only first name and a dedicated `info@diytracker.ch` contact address (no street address, consistent with a non-commercial CH hobby project having no Impressumspflicht); Datenschutz was trimmed from the earlier draft down to what actually needs disclosing under revDSG (account data, submitted content, and the scrape-detector's IP/user-agent/timestamp logging), dropping unused placeholder sections. AGB stays a WIP placeholder. Sitemap now includes both pages; the About page also shows the `info@diytracker.ch` contact address. DE/FR/IT/EN catalogs stay at 100% (one new string: "Contact").

## [0.34.0] - 2026-07-15
- Event JSON-LD is more complete for SEO: events without a free-text description now get one inferred from genre and lineup (e.g. "Doom Metal, Punk · Band A, Band B"); `endDate` defaults to `startDate` when no explicit end date is set (previously omitted for single-day events); and a `validFrom` date is now included, inferred from `created_at` (falling back to `updated_at` for the handful of pre-migration rows without one).

## [0.33.0] - 2026-07-15
- `manage_interactive.py`, a menu-driven wrapper around `manage.py`: numbered menus and prompts for every existing command (logs, user, venue, event, stats, db) so you don't need to remember subcommands or flags — it calls the exact same `cmd_*` functions manage.py's argparse dispatch would, so behavior (confirmation prompts, dry-run defaults, output) is identical.
- New `manage.py event dedup` command and backing `services/event_dedup.py`: finds events on the same date whose names share more than two words 1:1 (case/diacritic-insensitive) and offers to merge them one pair at a time (dry-run by default, `--apply` to confirm each merge). Merging repoints any scrape-queue approval link to the survivor, drops the losers' per-day view rows (can't be folded in without double-counting shared days), and deletes the losers — mirroring the existing venue-dedup command's shape (`find_dedup_candidates`/`select_survivor`/`merge_group` → `find_event_dedup_candidates`/`select_survivor`/`merge_events`).

## [0.32.1] - 2026-07-15
- Hotfix: `/events/397/` (a widely shared event deleted by mistake) 301-redirects to its recreated event `/events/557/` instead of 404ing.

## [0.32.0] - 2026-07-15
- Promoters can claim events already on the calendar: a new "Claim an event" page (`/promoter/claim`, linked from the dashboard's events section and its empty state) lists upcoming events that carry no label yet, each with a label picker and a Claim button that tags the event with one of the promoter's own labels — after which it appears on the label page and in the dashboard stats like any labelled event. The claim form's choices are restricted to labels the promoter owns (admins: all labels), so claiming with someone else's label fails validation; events that already belong to a label can't be claimed away (the race between two promoters resolves to first-commit-wins, the loser gets a flash message). Claiming busts the page cache like other label writes. DE/FR/IT catalogs stay at 100% (ten new strings).

## [0.31.0] - 2026-07-15
- Unified "backstage" layout for every logged-in tool page: `base_admin.html` became `base_backstage.html` and now hosts the admin pages, the submitter pages (`/submit`, `/queue`) and the promoter area (`/promoter/`, label forms) alike — previously the submitter pages wore the admin chrome (with a nav full of links that 403 for non-admins) while promoters managed labels under the full public banner. The backstage keeps the utilitarian style (mono, dark top bar, no grain/banner, `noindex`); page headers keep the shared `font-richEatin` convention, and the promoter dashboard lost its now-redundant back-to-calendar footer link.
- Role-gated backstage nav (`_partials/nav_backstage.html`, replacing `nav_admin.html`): one bar for all roles, showing only the links the user may actually use — everyone gets ← Calendar / Submit / Queue / Logout, promoters add Labels, admins add a visually separated group (Events — the renamed "Admin" dashboard link — Venues, Users, Analytics, Statistics, Suspects). The current page is highlighted (`aria-current="page"`; edit pages highlight their parent via a `nav_active` template variable), and on small screens the link list collapses into a no-JS `<details>` Menu disclosure with Calendar and Logout staying visible.
- Shared form macros in `_partials/forms.html` (`field`, `form_errors`, `submit_row`, plus the `LBL`/`INP`/`INP_COMPACT` class constants): one source of truth for the label/input styling and the validation-error block that were copy-pasted as `{% set lbl %}/{% set inp %}` strings across six templates. Converted: the shared event form, label form, venue edit form, the queue's inline override editor, and the public accessibility form.
- Admin URL cleanup: `/edit_event/<id>` and `/delete_event/<id>` moved under `/admin/` where the rest of the admin routes live; the old edit URL 301-redirects for existing bookmarks (the delete route is POST-only, nothing to redirect).
- DE/FR/IT/EN catalogs stay at 100% (one new string: the mobile nav's "Menu").

## [0.30.0] - 2026-07-15
- Promoter accounts and record labels: `Submitter` grew an `is_promoter` flag (granted via `manage.py user promoter <email> --grant/--revoke`, shown in `user list`, or toggled on the new `/admin/users` page — the first user-management page in the web admin, deliberately limited to the promoter flag since account creation and admin rights stay CLI-only). A promoter owns one or more labels (new `Label` model: unique name, stored slug, optional logo uploaded through the same validation/resize pipeline as event flyers) managed from a new `/promoter/` area (`promoter_required` decorator; admins pass everywhere and see all labels). Renaming a label regenerates its slug — the old page URL stops working, which the edit form says out loud. Deleting a label detaches its events explicitly (SQLite FK enforcement is off in this app). New columns via `migrations/migrate_add_promoter_labels.py`; the `label` table itself comes from `create_all()`.
- Public label pages at `/label/<slug>/`: the label's logo and name over its upcoming shows in the same stapled date-card layout as the main calendar (shared grouping helpers extracted from `calendar_view`; the month span follows the label's events instead of the calendar's fixed 3-month window), with a back-to-calendar link. Unlike genre pages, label pages stay live with zero upcoming events — promoters share the URL before their first show is posted — rendering an empty state instead of 404ing. Cached per path+locale (the archive's cache key, renamed `_path_locale_cache_key`), listed in the sitemap with `lastmod`, and `label`/`promoter` joined the reserved slugs so canton pages can never shadow them.
- Optional label on event submission: the shared event form (public submit + admin edit) gained a Label dropdown, hidden until at least one label exists. Any logged-in submitter can tag a show with any label — the label page and promoter stats deliberately count those too. `create_event()` takes a defaulted `label_id` kwarg, kept out of the dedup hash so existing events don't re-hash; the scrape-queue approval path is untouched and leaves events unlabelled. Promoter submissions publish immediately like all logged-in submissions — that required no change, just this sentence.
- Promoter dashboard at `/promoter/`: the promoter's labels (with page links, edit/delete) and a per-event traffic table over every event carrying their labels — views and daily-unique visitors from the existing `EventDailyViews` nginx-log pipeline (trend, not census, same caveat as the admin statistics page), newest first with a "past" badge, plus a copy-link button per event that puts the canonical `/events/<id>/` URL on the clipboard. No new tracking was added: opens of a shared event link are exactly what the page-view counts measure.
- The public header now only shows "Admin" to actual admins (previously every logged-in user saw it and non-admins got a 403), promoters get a "Labels" link, and plain submitters a "Submit" link. DE/FR/IT catalogs shipped at 100% with the ~40 new strings.

## [0.29.0] - 2026-07-14
- Public event archive at `/archive/`: a month index (per-year sections, month links with event counts) and per-month pages (`/archive/2026/6/`) listing past events grouped by day, with prev/next month navigation and a visible footer link. "Past" means strictly before the start of today, a stable boundary for the page cache. Not to be confused with `/events/archive/` — that stays the scrape-detection honeypot, untouched and regression-tested; the real archive uses distinct paths and endpoint names, and `archive` joined the reserved slugs so no canton page can ever shadow it. Archive pages are cached per path+locale like the calendar and listed in the sitemap with the same two-year horizon as past event pages. A new `services/archive.py` follows the cantons/genres directory pattern (memoized, busted by the write-path cache invalidation). Genre/canton filters on archive pages were considered and deferred.
- Per-event view counts, persisted: a new `event_daily_views` table (auto-created by `db.create_all()`; `event_id` deliberately not a foreign key so rows survive event deletion) accumulates per-(event, day) hits and unique-IP visitors for `/events/<id>/` pages. `services/event_views.py` parses the nginx logs directly in Python instead of goaccess — the goaccess requests panel only reports whole-window per-URL totals, which shrink as days rotate out of the logs and can't be accumulated safely — and rides the existing 15-minute analytics scheduler. Every run recomputes all days still inside the 60-day log window and upserts with `MAX(stored, new)`, so reruns are idempotent, today's partial counts only grow, and days that rotate out of the logs are simply never touched again: that untouched tail is the durable history (accumulating from July 2026 onward). Bot filtering is a UA denylist approximating goaccess `--ignore-crawlers`, so numbers differ slightly from the Traffic panel; both are trends, not a census. The honeypot path never matches the counted-path regex.
- New admin statistics page at `/admin/statistics` ("Statistics" in the admin nav): most-viewed events ranked by the persisted counts (linked, with hits/visitors and a "past" badge; deleted events show as such), events per month over the last 24 months as an inline-SVG bar chart plus yearly totals, distribution of all events (past included) by parent genre, by canton (via the same `resolve_canton()` normalization the canton pages use) and by busiest venues as ranked bar lists, and approximate ticket-price stats (median/mean/min–max CHF, free/donation and unparseable shares, price-bucket bars) parsed through the existing `seo.parse_price()` — ranges count their lower bound, so explicitly approximate. Backing queries live in a new memoized `services/db_stats.py`.
- Translation catalogs are back at 100%: the archive and statistics strings shipped translated (DE/FR/IT), and the 0.30.0 admin Traffic panel strings that had been left untranslated were filled in along the way.
- Live traffic numbers on the admin dashboard: a new Traffic section shows visitor and hit tiles (today with a yesterday reference line, last 30 days with a signed percent-growth delta against the previous 30 days) plus two inline-SVG bar charts of visitors/day and hits/day over the last month — no chart library, today's bar accented. Backing it is a new goaccess JSON pipeline in `services/analytics.py`: one `--ignore-crawlers` run over the last 60 days of nginx logs, with all windows (today/yesterday/7d/30d/previous-30d) derived from the per-date visitors panel and cached atomically to `instance/analytics_stats.json`. A daemon thread refreshes the cache every 15 minutes, riding the existing first-gunicorn-worker gate (`ENABLE_SCRAPER` now means "this worker runs the background jobs"); the admin-only `/admin/analytics/stats` endpoint serves the cache, and if it's missing or the scheduler looks dead (dev mode, dead first worker) it kicks a one-shot background regeneration rather than ever running goaccess inline in a request. The dashboard polls every 60 s. Multi-day visitor totals are sums of daily uniques, same as goaccess's own visitors panel — a trend indicator, not a census. The on-demand HTML report flow (`/admin/analytics`) is unchanged.
- With traffic promoted to the top of the dashboard, the auto-scrape status pill moved from the action bar to a borderless footnote strip below the events table (same element ids, so the existing progress-polling script is untouched).
- fail2ban reference configs in `deploy/`: `fail2ban-diytracker-filter.conf` (a filter for the "Failed login attempt" warnings the auth blueprint writes to `logs/error_log_diytracker` — the logged IP is the real client thanks to ProxyFix, and the filter's end-anchored regex can't be spoofed by an email crafted to look like a log line) and `fail2ban-diytracker-jail.conf` (enables the stock `sshd` jail plus a `diytracker-login` jail: 5 failures in 10 minutes → 1 h ban, doubling on repeat up to a week — complementing the existing Flask-Limiter 10/min cap on POST /login with actual firewall bans). Both carry install/test/unban instructions in their headers, same reference-copy convention as the nginx and systemd files.

## [0.28.0] - 2026-07-13
- New `deploy/traffic_audit.sh`, a read-only companion to `security_audit.sh` for judging whether traffic growth is a real audience or bots: it parses all nginx access logs (rotated and gzipped included) and reports the daily requests-vs-unique-IPs trend with per-day top-IP share, traffic concentration across the top IPs, peak single-IP requests/minute, scripted and rotating user agents, claimed-crawler share with a reverse-DNS spot-check of the heaviest "Googlebot" (fakes are common), status-code and 404 breakdowns, probes for known-exploit paths (`wp-login`, `.env`, `.git`, …), POST-endpoint abuse, and external referrers — analysis limited to a recent window (`DAYS`, default 30, `0` = everything) so old incidents in rotated logs don't skew the verdicts, each check ending in an OK/WARN verdict with the concrete mitigation (fail2ban jail, nginx `limit_req`, UFW block) when something looks off.
- `traffic_audit.sh` follow-ups after running it against the real prod log: an "estimated real users" overview (per-day count of IPs that look human — browser UA with no bot/scripted token, ≤5 distinct UAs, no exploit-path probes, at least one 2xx/3xx, under 500 requests — explicitly a trend indicator, not a census, since IPs are shared and hopped); a fix for the script finding nothing on prod (Debian 12's mawk predates `{n,m}` regex-interval support, so the date-window filter silently dropped every line — all regexes are now brace-free); and a big speedup on multi-year logs by decompressing and window-filtering once into a temp file instead of per analysis.
- `/robots.txt` now bans Awario's brand-monitoring crawler (`AwarioBot`/`AwarioRssBot`/`AwarioSmartBot`, `Disallow: /`): the July 2026 traffic audit showed it as the single heaviest client at ~17% of all requests, with no value in return.
- `manage.py` grew an admin toolkit beyond users/venues: `stats leaderboard` ranks submitters by contributed events with `--timeframe 7d|month|3months|all` (plus an unattributed-events row so totals reconcile), `stats overview` prints one-shot totals (events upcoming/past, venues, users/admins/missing passwords, pending scrape queue, skipped URLs, DB size), `event list|status|delete` covers the common moderation actions without opening the web admin (status/delete bust the page cache like the web routes do), and `db backup`/`db vacuum` snapshot the SQLite file via the WAL-safe online backup API and compact it. Leaderboard timeframes needed a real creation timestamp — `updated_at` resets on every edit — so `Event.created_at` was added with `migrations/migrate_add_created_at.py` (backfills existing rows from `updated_at`; the leaderboard refuses to run on an unmigrated schema, same pre-flight pattern as venue dedup).

## [0.27.0] - 2026-07-12
- Genre landing pages, the follow-up deferred in 0.22.0: `/genre/<slug>/` for every parent genre with at least one upcoming event (`/genre/metal/`, `/genre/goth-industrial/`, …; "Other" deliberately gets no page — a catch-all makes a thin, incoherent landing page). A new `services/genres.py` mirrors `services/cantons.py`: a memoized `genre_directory()` built from the indexed comma-sentinel `Event.parent_genres` column, busted by the same write-path cache invalidation. Each page lists upcoming events, the venues they happen at, and links the cantons they happen in (intersected with live canton pages), reusing the canton page's layout and voice. `genre` is now a reserved first path segment so the canton route can't swallow it. Genre×canton combo pages were considered and deferred: up to 11×26 mostly-thin URLs is doorway-page territory.
- Internal links into the new pages: the footer gains a Genres list (top 6 by upcoming-event count) next to the Cantons list, event pages link each of their parent genres ("more Punk shows"), and the sitemap emits genre URLs alongside canton ones.
- SEO copy pass over existing pages, keeping the non-corporate voice: the base template's default meta description was hardcoded German and is now translated per locale; the homepage title flips keywords in front of the brand ("DIY & punk concerts in Switzerland · diytracker.ch") with a matching meta description ("… one calendar, no ads, no algorithm.") and gains a screen-reader-only H1 (the banner image stays the visual identity, but the page previously had no H1 at all); event page titles append the venue's city for band+city queries and venue page titles append "concerts in <city>" (both skipped when the city is empty — real prod data has such venues); the canton H1 aligns with its title ("DIY & punk concerts in <canton>"); the map page's keyword-free "Map" title becomes "Venue map — DIY venues in Switzerland"; the about meta description names what the site is; and the banner alt text now says "diytracker — DIY concerts in Switzerland" instead of "DIY Tracker".

## [0.26.1] - 2026-07-12
- `manage.py venue dedup` catches more duplicate shapes found in the prod data. Name and city normalization now fold diacritics (NFKD, plus ß→ss), so "Bahnhofli"/"Bahnhöfli Biel" pass the containment check and "chateau d'erguël"/"Château d'Erguël" are recognized as the same name; city normalization also strips a trailing canton abbreviation ("Bremgarten AG" → "bremgarten"), putting such venues in the same city bucket as their unadorned twins. A new same-city/same-address signal (street part before the first comma, so "Hohlstrasse 457" matches "Hohlstrasse 457, 8048 Zürich") flags pairs like "Komplex 457"/"Komplex Klub" whose names share nothing. All new matches go to the interactive confirmation phase, never auto-merge — same-address pairs in particular can be genuinely distinct venues in one building (Reitschule Bern's Cafete/Rössli/Vorplatz).

## [0.26.0] - 2026-07-11
- Extracted a real event service: `diytracker/services/events.py` now owns `create_event()` (builds the Event with its dedup hash, optionally rejecting hash duplicates), `resolve_venue_from_form()` (the existing-id-or-get-or-create venue block) and `clean_genre_string()`, replacing three hand-rolled copies of the same DB writes in the queue-approve, submit and admin-edit routes. Queue-approval overrides reuse `ingest.parse_time` for doors (the date override deliberately stays a datetime — a bare date would change the dedup hash). The queue's filter-preserving redirect args got a small helper too.
- De-duplicated `submit_event.html`/`edit_event.html` (~90% identical, ~640 lines combined) into a shared `_partials/event_form.html` + `_partials/event_form_scripts.html` + `static/js/event_form.js`; the two pages are now thin wrappers passing the label/status/preselect differences. This also fixes venue selection on /submit: `form.hidden_tag()` was already emitting an empty hidden `venue_id` before the `<select name="venue_id">`, so WTForms always bound the empty value and picking an existing venue silently created/reused one from the free-text fields instead — the select is now nameless and mirrors into the single hidden input, as the edit page already did.
- More template dedup: the three identical legal wrapper templates and their three identical "WIP" routes collapsed into one `legal.html` + one `public.legal` route (`/<lang>/<doc>` with an `any()` converter, so the URL space and 404 behaviour are unchanged); the accessibility CTA (venue/event pages) and the "Back to …" footer block (4 pages) became partials; the Leaflet Switzerland-cutout code shared by /map and the venue mini-map moved into `static/js/ch_map.js` + `_partials/ch_map_styles.html` (shared `.ch-map` class), leaving only page-specific size/marker/interaction styles inline.
- Small helper dedup: `split_leading_plz()` in utils replaces three independent leading-PLZ regexes (canton inference, venue dedup normalisation, metalgigs location parsing); `_dedup_preserving_order()` replaces three copy-pasted seen-set loops in `scrape_events.py`; `i18n_status.py` now imports `build_source_catalog()` from `i18n_extract.py` instead of re-implementing the extraction loop; the tailwind safelist dropped seven string entries fully covered by its regex pattern; the `_parse_swiss_coords` re-alias in `public.py` is gone.
- Stale scripts: deleted `scripts/insert_dummy_events.py` (unguarded `Event.query.delete()`, pre-canton-registry seed data in formats the app no longer uses); moved the one-off geocoding backfills to `scripts/archive/` with a README; cross-referenced the intentionally duplicated `eventbot_to_payload()` in `import_eventbot.py`/`eventbot_forwarder.py` (the forwarder is stdlib-only on another machine and can't import the shared copy).

## [0.25.0] - 2026-07-11
- Restructured application startup around an app factory to make the codebase safer to change and easier to contribute to. `diytracker/app.py` now exposes `create_app()` and has no import-time side effects — previously merely importing the module loaded `.env`, ran `db.create_all()` against whatever `DATABASE_URI` happened to be set, and (with `ENABLE_SCRAPER=1`) started the scraper thread, which is why `manage.py`, the tests, and every script had grown set-env-vars-before-import workarounds. Config moved into a new `diytracker/config.py` (`Config.from_env()` is the single place environment variables are read; a missing `SECRET_KEY` now raises a clear "copy .env.example" error instead of a bare `KeyError`), and entrypoints own their side effects: the root `app.py` (gunicorn target `app:app`, unchanged for deployment) builds the app and is the only place the scrape scheduler starts, `manage.py` builds its own app (no more `os.chdir` or pre-import env mutation; `venue dedup --db-path` passes the override through config instead of the environment), and `tests/conftest.py` shrank to `create_app(TestConfig(...))` with no scraper monkeypatching. The scrape detector's kill switch is now config-driven (`SCRAPE_DETECTION_ENABLED`).
- Importing `diytracker/services/scrape_events.py` (the web app needs only its parsers) no longer mutates `sys.path` or attaches a DEBUG file handler writing `logs/scrape_events.log` in every gunicorn worker: logging setup moved into `configure_logging()`, called only by actual scrape runs (the scheduler and the standalone CLI).
- Contributor onboarding: `.env.example` now documents every environment variable the app reads (including `CANONICAL_HOST`, which was missing), and the README gained an Architecture section describing the factory/entrypoint split; the environment section is now just `cp .env.example .env`.

## [0.24.1] - 2026-07-10
- Made `manage.py venue dedup` safe on databases that predate the SEO columns (`event.status`, `event.updated_at`, `venue.updated_at`): a pre-flight check compares the live schema against the models and refuses to start — pointing at `migrations/migrate_add_seo_columns.py` — instead of dying mid-run with an `OperationalError` (`db.create_all()` never adds columns to existing tables). Merges now also keep the sitemap `<lastmod>` honest: the bulk event repoint bumps `updated_at` by hand (bulk UPDATEs bypass the ORM `onupdate`), and the surviving venue is touched even when no fields are backfilled, since it absorbs the losers' events.

## [0.24.0] - 2026-07-10
- Replaced the city landing pages with canton landing pages: many DIY venues sit in small towns that would never earn their own page, and the biggest cities share their canton's name anyway, so `/zuerich/` now means the canton and aggregates shows from Zürich, Winterthur, Uster, …. Pages live at the same bare-slug URLs (`/bern/`, `/basel-stadt/`, `/geneve/`), slugged from a new canonical 26-canton registry (`CANTONS` in `diytracker/utils.py`) and localized per locale (Genève/Genf/Ginevra). Venues are grouped by normalizing `Venue.canton` at read time via the existing `resolve_canton()` (codes, full DE/FR/IT names, city-name inference for null cantons — no DB migration), so `services/cities.py` became `services/cantons.py`. Event and venue rows on the page now show each venue's city, the footer lists the top cantons, event pages link "More events in <canton>", and the sitemap emits canton URLs. SEO parity is kept (H1/title/meta description per canton, canonical URLs, sitemap `daily` entries); old city-only slugs like `/winterthur/` now 404 without redirects — the city pages had been live for less than a day, so there was no link equity worth preserving.
- New footer slogan: "No ads. No sponsors. No algorithm." (DE "Keine Werbung. Keine Sponsoren. Kein Algorithmus.", FR/IT translated accordingly) replaces "Underground. Non-commercial. Hand-curated." across all four locales.

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
