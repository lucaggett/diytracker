# diytracker

A Flask web app that aggregates DIY / underground concert listings in
Switzerland. Events come in two ways:

1. User submissions: invited users (there is no public signup) fill
   out a form and the event goes live immediately.
2. Scrapers: a background thread periodically pulls event pages from
   external sites (currently `metalgigs.ch` and `petzi.ch`), parses
   them, and stores them as `ScrapedEvent` rows so an admin can
   convert them into real `Event` rows.

It also tracks per-venue accessibility info and is translated into
DE / EN / FR / IT via Flask-Babel.

## Running it yourself

### Prerequisites

- [uv](https://docs.astral.sh/uv/) (manages Python + dependencies)
- Node.js, only to rebuild Tailwind CSS. Not needed if you keep the
  prebuilt `static/css/output.css`.

### Setup

```bash
git clone <this-repo> diytracker
cd diytracker

# Creates .venv and installs the locked dependencies from uv.lock.
# uv will fetch the pinned Python version automatically if needed.
uv sync

# Tailwind (only needed if you edit templates or tailwind.config.js)
npm install
npx @tailwindcss/cli -i ./static/css/styles.css -o ./static/css/output.css --minify --watch
```

> uv does not activate the venv. Run project commands through `uv run`
> (e.g. `uv run python app.py`). A bare `gunicorn` or `python` will not
> see the project's dependencies.

### Environment

Create a `.env` file in the project root (loaded by `python-dotenv`):

```
SECRET_KEY=<any long random string>

# Only required if you want the contact form to send mail
EMAIL_SERVER=smtp.example.com
EMAIL_USERNAME=...
EMAIL_PASSWORD=...

# Optional: where analytics reads nginx logs from
NGINX_LOG_PATTERN=access.log

# Only required to accept pushes on POST /api/ingest (see "Ingest");
# unset disables the endpoint.
INGEST_TOKEN=<any long random string>
```

The background scraper only runs when `ENABLE_SCRAPER=1` is set for the
process. Under gunicorn you don't set it yourself: `gunicorn_conf.py`'s
`post_fork` hook enables it for exactly one worker. For the dev server use
`ENABLE_SCRAPER=1 uv run python app.py` (don't put it in `.env`, or every
gunicorn worker would start its own scheduler).

The SQLite database (`instance/events.db`) is created automatically on
first run via `db.create_all()` in `app.py`.

### Run

Dev server:

```bash
uv run python app.py            # http://127.0.0.1:5001
```

Production (matches `gunicorn_conf.py`):

```bash
uv run gunicorn -c gunicorn_conf.py app:app
```

### Managing the server

On the production server the app runs under systemd; see
[`deploy/README.md`](deploy/README.md) for the unit file, the login MOTD,
and install steps. Use `systemctl {status,restart} diytracker` there.

`manage.py` is a small CLI for tailing logs and managing users. Run it
through uv:

```bash
uv run python manage.py logs -f        # tail the access log (--error for the error log)
```

### Managing users

There is no signup. Use the `user` subcommands of `manage.py`:

```bash
uv run python manage.py user list
uv run python manage.py user add alice@example.com        # creates + emails an invite (--no-email to print the link)
uv run python manage.py user passwd alice@example.com     # prompts for a password
uv run python manage.py user admin alice@example.com --grant   # or --revoke
uv run python manage.py user invite alice@example.com     # resend the invite email
uv run python manage.py user delete alice@example.com     # --yes to skip confirmation
```

## Ingest

Events reach the site from three kinds of sources, all funnelling
through `services/ingest.py` into the `ScrapedEvent` staging queue,
where an admin approves them into real `Event` rows:

1. User submissions (`/submit`), the only path that creates `Event`
   rows directly without going through the queue.
2. Built-in scrapers (metalgigs, petzi; see "Scrapers" below).
3. External pushers via `POST /api/ingest`, e.g. the Signal flyer bot
   ("eventbot") running on another machine.

`ingest_event()` owns validation (title and a `YYYY-MM-DD` start date
are required), normalisation (canton, genre tokens, ticket URLs),
dedup (by canonical `url`, or by `(source, source_id)` for sources
without URLs) and flyer storage: a pushed image is validated, resized
and stored exactly like a user-uploaded flyer, shows up as a thumbnail
in the approval queue, and is carried onto the `Event` when approved.

### Push API

`POST /api/ingest` authenticates with `Authorization: Bearer
<INGEST_TOKEN>` (set `INGEST_TOKEN` in `.env`; if unset, the endpoint
is disabled and returns 503). Two body shapes:

```bash
# JSON only
curl -X POST https://diytracker.ch/api/ingest \
  -H "Authorization: Bearer $INGEST_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"source":"eventbot","source_id":"abc123","title":"Grind Night",
       "start_date":"2026-08-01","venue_name":"Ebrietas","city":"Zürich"}'

# multipart: event JSON + flyer image (png/jpg/gif)
curl -X POST https://diytracker.ch/api/ingest \
  -H "Authorization: Bearer $INGEST_TOKEN" \
  -F 'event={"source":"eventbot","source_id":"abc123","title":"Grind Night","start_date":"2026-08-01"}' \
  -F 'flyer=@flyer.jpg'
```

The full payload key list is documented in `services/ingest.py`.
Responses: `201` created, `200` duplicate (idempotent, so safe to mark
delivered), `422` invalid, `401` bad token. Pushers should treat 200
and 201 as success and 422 as a permanent rejection.

### The Signal eventbot

The eventbot (Crowdkill Report, on riggi-lab at `~/.hermes/eventbot/`)
watches a Signal flyer group and keeps a source-agnostic store of
parsed events plus the original flyer images. Two scripts connect it:

- `scripts/eventbot_forwarder.py` runs on the eventbot box via cron
  and POSTs each new record (content + flyer) to `/api/ingest` once,
  tracking delivery in a state file. It uses only the stdlib, so you
  can just copy it over.
- `scripts/import_eventbot.py` is a one-shot local backfill from an
  rsync'd copy of the store (see its docstring).

## Scrapers

### How they're organised

The scraping pipeline has two pieces:

| File | Role |
| --- | --- |
| `services/scrape_events.py` | Pure scraping logic. One function per source that takes a URL and returns a dict. Also has helpers to discover URLs from each source's sitemap. Can be run standalone (`uv run python services/scrape_events.py`) to dump everything to `instance/events.csv`. |
| `services/scraper.py` | The runtime glue. Discovers new URLs, calls the per-source parsers, deduplicates against existing `Event.source_url` and `ScrapedEvent.url`, then writes new `ScrapedEvent` rows. Also owns the background scheduler thread (`start_auto_scheduler`, called from `app.py`) which re-runs the scrape every `SCRAPE_INTERVAL_HOURS`. |

Scraped events live in their own table (`ScrapedEvent`). They aren't
shown to the public until an admin approves one in the event queue,
which copies its fields into a real `Event` row and sets
`approved_event_id` on the source.

### Adding a new scraper

1. **Write a parser in `services/scrape_events.py`.** It should take an
   event URL and return a dict using the same keys as the existing
   parsers (`source`, `url`, `title`, `performers`, `styles`,
   `description`, `start_date`, `end_date`, `doors_open`,
   `start_time`, `venue_name`, `street_address`, `city`, `region`,
   `postal_code`, `ticket_price`, `ticket_currency`, `ticket_url`,
   `organizer`, `event_status`). Dates as `YYYY-MM-DD`, times as
   `HH:MM`. Use `fetch_url()` for HTTP; it handles user-agent
   rotation, jittered delays, and 403/429 backoff. Use
   `resolve_canton(region, city)` to normalise the Swiss canton.

2. **Write a URL discovery function.** If the source has a sitemap
   you can reuse `get_sitemap_event_urls(sitemap_url, pattern)`;
   otherwise write a one-off helper (see `get_petzi_event_urls` for
   the pattern).

3. **Wire it into `services/scraper._scrape_and_import`.** Import
   your new functions where the existing ones are imported, collect
   the URLs, and add a tuple to the `all_urls` list:

   ```python
   ('mysource', url, parse_mysource_event) for url in mysource_urls if url not in known_urls
   ```

   The dedup / cleanup / DB insert below the list is source-agnostic
   and will pick it up automatically. If your source needs special
   filtering (see the petzi `'concert'` filter), add it next to the
   existing one.

4. **(Optional) extend the standalone runner** in `main()` of
   `services/scrape_events.py` if you want `python services/scrape_events.py`
   to also include the new source.

No schema or admin-side changes are needed; new rows show up in the
admin event queue on the next scrape tick.

## Architecture

```
app.py                   Flask app factory-ish: config, blueprint registration,
                         starts the scraper scheduler thread.
models.py                SQLAlchemy models: Event, Venue, VenueAccessibility,
                         Submitter, ScrapedEvent. Includes a before_insert/update
                         hook that auto-derives Event.parent_genres from .genre.
forms.py                 Flask-WTF form definitions.
utils.py                 Shared helpers (genre tokenising, canton resolution,
                         ticket URL cleanup, etc.).

blueprints/
  public.py              Public-facing pages: calendar, venues, accessibility,
                         contact form, sitemap.
  submissions.py         The "submit an event" flow for non-admins, plus the
                         scraped-event approval queue.
  auth.py                Login, logout, invite-token password setup.
  admin.py               Admin dashboard: event/venue editing, Excel export,
                         analytics, weekly calendar image.
  api.py                 JSON endpoints: genre/venue lookups, POST /api/ingest.

services/
  scraper.py             Background scheduler + import pipeline (see above).
  i18n.py                Locale selection + a tiny gettext fallback for when
                         flask-babel isn't installed.
  cache.py               Flask-Caching wrapper.
  contact.py             SMTP for the contact form.
  events.py / venue.py   Shared business logic used by multiple blueprints.
  uploads.py             Flyer upload handling.
  analytics.py           goaccess-style log report glue.
  calendar_image.py      OG-image generation for calendar pages.
  scrape_events.py       Source-specific scraping (see "Scrapers" above).

scripts/                 Operational tools: eventbot forwarder/importer, i18n
                         extraction, email config checks, dummy data, etc.
                         Run with `uv run python scripts/<name>.py`.

migrations/              Hand-written one-shot migration scripts. There's no
                         Alembic env wired up; the table itself is created
                         by `db.create_all()` and each migration file adds
                         specific columns/indexes for an upgrade step.

templates/               Jinja templates. `base_public.html` and
                         `base_admin.html` are the two layouts; `_partials/`
                         holds shared fragments.

static/                  Tailwind output, fonts, favicon, user-uploaded
                         flyers (under `static/uploads/`, gitignored).

translations/            Babel `.po` / `.mo` files for de / en / fr / it.
                         `babel.cfg` configures extraction.

instance/                SQLite DB, scrape state (`last_scrape.txt`),
                         cache dir, CSV output. Gitignored.
```

### Request lifecycle

A request hits Flask, `_bind_locale_to_g` picks a locale (from
`?lang=`, session, `Accept-Language`, in that order; see
`services/i18n.py`), and the matching blueprint handles it. Public
calendar responses are cached for 5 minutes via `Cache-Control` and
also through `flask-caching` keyed on locale.

### Background work

`start_auto_scheduler(app)` is called at import time, but only when
`ENABLE_SCRAPER=1` is set (see "Environment"). It spawns a daemon
thread that loops forever, waking every `SCRAPE_INTERVAL_HOURS`
to run `_scrape_and_import`. The scraper persists its state in
`instance/last_scrape.txt` so restarts don't trigger immediate
re-scrapes, and exposes progress via `services.scraper.get_progress()`
for the admin UI.
