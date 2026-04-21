import calendar
import functools
import hashlib
import io
import os
import threading
import time as time_module
from collections import defaultdict
from datetime import datetime, time as time_type, timedelta
from dateutil.relativedelta import relativedelta

from dotenv import load_dotenv
from flask import Flask, render_template, redirect, url_for, flash, request, jsonify, abort, session, send_file, g
from werkzeug.utils import secure_filename

from forms import EventForm, EventEditForm, LoginForm, DeleteEventForm, SetPasswordForm, CollaboratorRequestForm
from models import db, Event, Venue, Submitter, ScrapedEvent
from utils import resolve_canton, parent_genres, clean_genre_tokens

# Flask-Babel is optional at import time so the app keeps booting even before
# the dependency is installed.  With the real package available, {{ _('…') }}
# is translated; without it, strings pass through unchanged.
try:
    from flask_babel import Babel, gettext as _babel_gettext  # type: ignore
    _HAS_BABEL = True
except ImportError:  # pragma: no cover - defensive fallback
    Babel = None  # type: ignore
    _HAS_BABEL = False
    def _babel_gettext(s, **kwargs):
        return s % kwargs if kwargs else s

SUPPORTED_LOCALES = ('de', 'fr', 'en')
DEFAULT_LOCALE = 'de'

load_dotenv()

# Check that required directories exist
if not os.path.exists('logs'):
    os.makedirs('logs')

if not os.path.exists('static/uploads'):
    os.makedirs('static/uploads')

app = Flask(__name__)

app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///events.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = os.environ['SECRET_KEY']
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=7)

db.init_app(app)

# ---------------------------------------------------------------------------
# i18n (DE / FR / EN) — Flask-Babel with graceful degradation
# ---------------------------------------------------------------------------
app.config['BABEL_DEFAULT_LOCALE'] = DEFAULT_LOCALE
app.config['BABEL_SUPPORTED_LOCALES'] = list(SUPPORTED_LOCALES)
app.config['BABEL_TRANSLATION_DIRECTORIES'] = 'translations'


def select_locale():
    lang = session.get('lang')
    if lang in SUPPORTED_LOCALES:
        return lang
    best = request.accept_languages.best_match(list(SUPPORTED_LOCALES))
    return best or DEFAULT_LOCALE


if _HAS_BABEL:
    babel = Babel(app, locale_selector=select_locale)
else:
    app.jinja_env.globals['_'] = _babel_gettext
    app.jinja_env.globals['gettext'] = _babel_gettext


@app.before_request
def _bind_locale_to_g():
    try:
        g.locale = select_locale()
    except RuntimeError:
        g.locale = DEFAULT_LOCALE


@app.context_processor
def _inject_locale():
    return {'current_locale': getattr(g, 'locale', DEFAULT_LOCALE)}


@app.route('/set-language/<lang>')
def set_language(lang):
    if lang not in SUPPORTED_LOCALES:
        abort(404)
    session['lang'] = lang
    next_url = request.args.get('next', '')
    if next_url.startswith('/') and not next_url.startswith('//'):
        return redirect(next_url)
    return redirect(url_for('calendar_view'))


def login_required(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login', next=request.path))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login', next=request.path))
        user = db.session.get(Submitter, session['user_id'])
        if not user or not user.is_admin:
            abort(403)
        return f(*args, **kwargs)
    return decorated

with app.app_context():
    db.create_all()

app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['ALLOWED_EXTENSIONS'] = {'png', 'jpg', 'jpeg', 'gif'}

MAGIC_BYTES = [b'\xff\xd8\xff', b'\x89PNG\r\n\x1a\n', b'GIF87a', b'GIF89a']


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']


def validate_image_content(file_storage):
    header = file_storage.read(8)
    file_storage.seek(0)
    return any(header.startswith(m) for m in MAGIC_BYTES)


def _clean_genre(raw: str) -> str:
    return ', '.join(clean_genre_tokens(raw))


app.jinja_env.globals['parent_genres'] = parent_genres


# ---------------------------------------------------------------------------
# Scrape infrastructure
# ---------------------------------------------------------------------------
LAST_SCRAPE_FILE = os.path.join('instance', 'last_scrape.txt')
_scrape_lock = threading.Lock()
_scrape_running = False
_scrape_progress = {'total': 0, 'processed': 0, 'started_at': None, 'phase': ''}


def _get_last_scrape_time():
    try:
        with open(LAST_SCRAPE_FILE) as f:
            return datetime.fromisoformat(f.read().strip())
    except (FileNotFoundError, ValueError):
        return None


def _set_last_scrape_time(dt):
    os.makedirs(os.path.dirname(LAST_SCRAPE_FILE), exist_ok=True)
    with open(LAST_SCRAPE_FILE, 'w') as f:
        f.write(dt.isoformat())


def _load_scraper():
    """Load the scrape_events module from utils/ directory via importlib."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "scrape_events",
        os.path.join(os.path.dirname(__file__), "utils", "scrape_events.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _get_known_urls():
    """Return a set of all URLs already in the queue or calendar."""
    with app.app_context():
        scraped_urls = {r.url for r in ScrapedEvent.query.with_entities(ScrapedEvent.url).all() if r.url}
        event_urls = {r.source_url for r in Event.query.with_entities(Event.source_url).filter(Event.source_url.isnot(None)).all()}
        return scraped_urls | event_urls


def _scrape_and_import():
    """Run scrapers and insert only new events into the ScrapedEvent table."""
    global _scrape_running, _scrape_progress
    try:
        scraper = _load_scraper()
        get_sitemap_event_urls = scraper.get_sitemap_event_urls
        get_petzi_event_urls = scraper.get_petzi_event_urls
        parse_metalgigs_event = scraper.parse_metalgigs_event
        parse_petzi_event = scraper.parse_petzi_event
        from scripts.import_scraped_events import parse_date, parse_time

        _scrape_progress = {'total': 0, 'processed': 0, 'started_at': datetime.now().isoformat(), 'phase': 'Fetching sitemaps'}

        mg_urls = get_sitemap_event_urls("https://metalgigs.ch/sitemap.xml", "/konzerte/")
        petzi_urls = get_sitemap_event_urls("https://www.petzi.ch/en/sitemap.xml", "/en/events/")
        if not petzi_urls:
            petzi_urls = get_petzi_event_urls()

        # Filter out URLs already in the queue or calendar before making any HTTP requests
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
            # Re-fetch known URLs inside the app context for the safety check
            known_urls_now = _get_known_urls()
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
        _set_last_scrape_time(datetime.now())
    except Exception:
        app.logger.exception("Scrape failed")
    finally:
        with _scrape_lock:
            _scrape_running = False


SCRAPE_INTERVAL_HOURS = 1


def _auto_scheduler():
    """Background thread: run a scrape every SCRAPE_INTERVAL_HOURS hours."""
    global _scrape_running
    while True:
        last = _get_last_scrape_time()
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
        _scrape_and_import()
        time_module.sleep(SCRAPE_INTERVAL_HOURS * 3600)


_scheduler_thread = threading.Thread(target=_auto_scheduler, daemon=True, name='scrape-scheduler')
_scheduler_thread.start()


@app.route('/login', methods=['GET', 'POST'])
def login():
    form = LoginForm()
    if form.validate_on_submit():
        user = Submitter.query.filter_by(email=form.email.data).first()
        if user and user.check_password(form.password.data):
            session['user_id'] = user.id
            next_url = request.args.get('next', '')
            if next_url.startswith('/'):
                return redirect(next_url)
            return redirect(url_for('calendar_view'))
        flash('Invalid email or password.')
    return render_template('login.html', form=form)


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('calendar_view'))


@app.route('/set-password/<token>', methods=['GET', 'POST'])
def set_password(token):
    user = Submitter.query.filter_by(invite_token=token).first()
    if not user or not user.invite_token_expiry or user.invite_token_expiry < datetime.utcnow():
        flash('This invite link is invalid or has expired.')
        return redirect(url_for('login'))
    form = SetPasswordForm()
    if form.validate_on_submit():
        user.set_password(form.password.data)
        user.clear_invite_token()
        db.session.commit()
        session['user_id'] = user.id
        return redirect(url_for('submit_event_link'))
    return render_template('set_password.html', form=form)


@app.route('/get_genres')
def get_genres():
    from forms import get_genre_choices
    seed = [g for g, _ in get_genre_choices()]
    db_genres = []
    for event in Event.query.with_entities(Event.genre).filter(Event.genre != None).all():
        for g in (event.genre or '').split(','):
            g = g.strip()
            if g:
                db_genres.append(g)
    combined = sorted(set(seed + db_genres), key=str.lower)
    return jsonify({'genres': combined})


CONTACT_RECIPIENT = 'luc@aggett.com'


def _build_contact_logger():
    import logging
    logger = logging.getLogger('diytracker.contact')
    if not logger.handlers:
        handler = logging.FileHandler(os.path.join('logs', 'contact_form.log'))
        handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger


contact_logger = _build_contact_logger()


def _send_contact_email(name, sender_email, message):
    """Send a collaborator-request email. Raises on misconfig or SMTP failure."""
    import smtplib
    import ssl
    from email.message import EmailMessage

    server_host = os.environ['EMAIL_SERVER']
    username = os.environ['EMAIL_USERNAME']
    password = os.environ['EMAIL_PASSWORD']

    msg = EmailMessage()
    msg['Subject'] = f'[diytracker] Mitwirkenden-Anfrage von {name}'
    msg['From'] = 'info@diytracker.ch'
    msg['To'] = CONTACT_RECIPIENT
    msg['Reply-To'] = sender_email
    msg.set_content(
        f'Name:    {name}\n'
        f'E-Mail:  {sender_email}\n'
        f'\n'
        f'Nachricht:\n'
        f'{message}\n'
    )

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(server_host, 465, context=context) as server:
        server.login(username, password)
        server.send_message(msg)
    print("Email sent!")


@app.route('/about', methods=['GET', 'POST'])
def about():
    form = CollaboratorRequestForm()
    if form.validate_on_submit():
        name = form.name.data.strip()
        sender_email = form.email.data.strip()
        message = form.message.data.strip()
        honeypot_filled = bool((form.website.data or '').strip())
        single_line_message = message.replace('\n', ' \\n ')
        if honeypot_filled:
            contact_logger.info(
                'honeypot name=%r email=%r message=%r',
                name, sender_email, single_line_message,
            )
        else:
            try:
                _send_contact_email(name, sender_email, message)
            except Exception as exc:
                contact_logger.exception(
                    'send_failed name=%r email=%r message=%r error=%s',
                    name, sender_email, single_line_message, exc,
                )
                app.logger.exception('Failed to send collaborator email')
                flash('Nachricht konnte nicht gesendet werden — bitte schreib uns direkt an kontakt@diytracker.ch.')
                return redirect(url_for('about'))
            contact_logger.info(
                'sent name=%r email=%r message=%r',
                name, sender_email, single_line_message,
            )
        flash('Danke! Wir melden uns sobald wie möglich.')
        return redirect(url_for('about'))
    return render_template('about.html', form=form)


# ---------------------------------------------------------------------------
# Legal pages — locale-prefixed so each language has a stable, shareable URL.
# ---------------------------------------------------------------------------
def _validate_lang(lang):
    if lang not in SUPPORTED_LOCALES:
        abort(404)
    g.locale = lang


@app.route('/<lang>/impressum')
def impressum(lang):
    _validate_lang(lang)
    return render_template('impressum.html')


@app.route('/<lang>/agb')
def agb(lang):
    _validate_lang(lang)
    return render_template('agb.html')


@app.route('/<lang>/datenschutz')
def datenschutz(lang):
    _validate_lang(lang)
    return render_template('datenschutz.html')


# ---------------------------------------------------------------------------
# Error handlers
# ---------------------------------------------------------------------------
@app.errorhandler(404)
def _handle_404(_err):
    return render_template('errors/404.html'), 404


@app.errorhandler(500)
def _handle_500(_err):
    return render_template('errors/500.html'), 500


@app.route('/queue', methods=['GET', 'POST'])
@login_required
def event_queue():
    """Display and approve scraped events for a given submitter.

    GET: show the list of filtered events with approve buttons.
    POST: create the selected event in the database.
    """
    submitter = db.session.get(Submitter, session['user_id'])
    # Read filter params from query string
    def _parse_date(key):
        raw = request.args.get(key, '').strip()
        try:
            return datetime.strptime(raw, '%Y-%m-%d').date() if raw else None
        except ValueError:
            return None
    filter_date_from = _parse_date('date_from')
    filter_date_to   = _parse_date('date_to')
    filter_source    = request.args.get('source', '').strip() or None
    events = parse_scraped_events(
        date_from=filter_date_from,
        date_to=filter_date_to,
        source=filter_source,
    )
    if request.method == 'POST':
        # Expect an index pointing into the events list.  Each event
        # dictionary stores `_scraped_id` which references the primary
        # key of the ScrapedEvent record.
        try:
            idx = int(request.form.get('index'))
            data = events[idx]
        except (ValueError, IndexError):
            flash('Invalid event selection.')
            return redirect(url_for('event_queue'))
        # Apply overrides from inline edit form (fall back to scraped data)
        def _ov(key, fallback):
            val = request.form.get(key, '').strip()
            return val if val else fallback

        name         = _ov('override_name', data.get('title') or data.get('performers') or 'Concert')
        venue_name   = _ov('override_venue_name', data.get('venue_name') or 'Unknown venue')
        city         = _ov('override_city', data.get('city') or '')
        plz          = _ov('override_postal_code', data.get('postal_code') or '')
        street       = _ov('override_street_address', data.get('street_address'))
        raw_genre    = _ov('override_genre', data.get('styles') or '')
        genre        = _clean_genre(raw_genre)
        acts         = _ov('override_acts', data.get('performers') or '')
        ticket_price = _ov('override_ticket_price', data.get('ticket_price') or '')
        ticket_link  = _ov('override_ticket_url', data.get('ticket_url') or data.get('ticket_link') or '')
        description  = _ov('override_description', data.get('description') or '')
        source_url   = _ov('override_source_url', data.get('url') or '')

        override_date = request.form.get('override_date', '').strip()
        event_date = datetime.strptime(override_date, '%Y-%m-%d') if override_date else data.get('_event_date')

        override_doors = request.form.get('override_doors', '').strip()
        if override_doors:
            try:
                doors_time = datetime.strptime(override_doors, '%H:%M').time()
            except ValueError:
                doors_time = time_type(19, 0)
        else:
            try:
                doors_time = datetime.strptime(data.get('doors_open') or '19:00', '%H:%M').time()
            except ValueError:
                doors_time = time_type(19, 0)

        # Create or fetch venue
        venue = Venue.query.filter_by(name=venue_name, city=city, plz=plz).first()
        if not venue:
            venue = Venue(
                name=venue_name,
                address=street,
                city=city,
                canton=resolve_canton(data.get('region') or '', city),
                plz=plz,
                coords=data.get('coords') or ''
            )
            db.session.add(venue)
            db.session.commit()
        # Compute a hash to avoid duplicates (similar to submit_event_link)
        event_hash = hashlib.sha256(
            f"{name}{event_date}{doors_time}{genre}{acts}{ticket_link}{ticket_price}{venue.id}".encode()
        ).hexdigest()
        # Check if event already exists
        existing = Event.query.filter_by(event_hash=event_hash).first()
        if existing:
            flash('This event already exists.')
            return redirect(url_for('event_queue'))
        new_event = Event(
            name=name,
            date=event_date,
            doors=doors_time,
            genre=genre,
            acts=acts,
            description=description or None,
            source_url=source_url or None,
            flyer=None,
            ticket_link=ticket_link,
            ticket_price=ticket_price,
            venue_id=venue.id,
            event_hash=event_hash,
            submitter_id=submitter.id
        )
        db.session.add(new_event)
        db.session.commit()
        # Mark the scraped event as approved and link it to the new Event
        scraped_id = data.get('_scraped_id')
        if scraped_id:
            scraped_obj = ScrapedEvent.query.get(scraped_id)
            if scraped_obj:
                scraped_obj.approved = True
                scraped_obj.approved_at = datetime.now()
                scraped_obj.approved_event_id = new_event.id
                db.session.commit()
        flash('Event approved and added to calendar!')
        # Preserve filter params through redirect
        redirect_args = {k: v for k, v in request.args.items()}
        return redirect(url_for('event_queue', **redirect_args))
    now = datetime.now().date()
    return render_template(
        'event_queue.html',
        events=events,
        filter_date_from=(filter_date_from or now).isoformat(),
        filter_date_to=(filter_date_to or (now + relativedelta(months=2))).isoformat(),
        filter_source=filter_source or '',
    )

@app.route('/submit', methods=['GET', 'POST'])
@login_required
def submit_event_link():
    """
    Allows event submission by logged-in submitters.
    """
    form = EventForm()
    submitter = db.session.get(Submitter, session['user_id'])

    if form.validate_on_submit():
        # Extract form data
        name = form.name.data
        date = form.date.data
        end_date = form.end_date.data
        is_festival = form.is_festival.data
        doors = form.doors.data
        genres = form.genre.data
        acts = form.acts.data
        ticket_link = form.ticket_link.data
        ticket_price = form.ticket_price.data

        # Upload flyer if present
        flyer = None
        if form.flyer.data:
            file = form.flyer.data
            if file and allowed_file(file.filename) and validate_image_content(file):
                filename = secure_filename(file.filename)
                flyer_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                file.save(flyer_path)
                flyer = flyer_path

        # Venue logic is unchanged from your existing code:
        venue_id = form.venue_id.data
        if venue_id == 'new' or not venue_id:
            # Create new venue or fetch existing
            venue_name = form.venue_name.data
            venue_address = form.venue_address.data
            venue_city = form.venue_city.data
            venue_canton = form.venue_canton.data
            venue_plz = form.venue_plz.data
            venue_coords = form.venue_coords.data

            if not venue_name or not venue_city or not venue_plz:
                flash('Please provide all required venue details for a new venue.')
                return redirect(url_for('submit_event_link'))

            venue = Venue.query.filter_by(
                name=venue_name,
                city=venue_city,
                plz=venue_plz
            ).first()

            if not venue:
                venue = Venue(
                    name=venue_name,
                    address=venue_address,
                    city=venue_city,
                    canton=venue_canton,
                    plz=venue_plz,
                    coords=venue_coords
                )
                db.session.add(venue)
                db.session.commit()
            else:
                flash('Venue already exists. Using existing venue.')
        else:
            venue = Venue.query.get(venue_id)
            if not venue:
                flash('Selected venue does not exist.')
                return redirect(url_for('submit_event_link'))

        # Create the Event
        new_event = Event(
            name=name,
            date=date,
            end_date=end_date,
            is_festival=is_festival,
            doors=doors,
            genre=', '.join(genres) if genres else '',
            acts=acts,
            flyer=flyer,
            ticket_link=ticket_link,
            ticket_price=ticket_price,
            venue_id=venue.id,
            event_hash=hashlib.sha256(
                f"{name}{date}{doors}{genres}{acts}{ticket_link}{ticket_price}{venue.id}".encode()
            ).hexdigest(),
            # Assign the submitter so we know who created it:
            submitter_id=submitter.id
        )
        db.session.add(new_event)
        db.session.commit()

        flash('Event submitted successfully!')
        return redirect(url_for('calendar_view'))

    return render_template('submit_event.html', form=form)


# ---------------------------------------------------------------------------
# Weekly calendar image
# ---------------------------------------------------------------------------

# 12-colour palette (vivid, readable on dark bg)
_GENRE_PALETTE = [
    (239,  68,  68),  # red
    (249, 115,  22),  # orange
    (234, 179,   8),  # yellow
    ( 34, 197,  94),  # green
    ( 20, 184, 166),  # teal
    (  6, 182, 212),  # cyan
    ( 59, 130, 246),  # blue
    (139,  92, 246),  # violet
    (236,  72, 153),  # pink
    (132, 204,  22),  # lime
    (245, 158,  11),  # amber
    ( 16, 185, 129),  # emerald
]


def _load_font(size, bold=False):
    """Load Arial (or a DejaVu fallback) at the requested size."""
    from PIL import ImageFont
    candidates = (
        ['/Library/Fonts/Arial Bold.ttf',
         '/System/Library/Fonts/Supplemental/Arial Bold.ttf',
         '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
         '/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf']
        if bold else
        ['/Library/Fonts/Arial.ttf',
         '/System/Library/Fonts/Supplemental/Arial.ttf',
         '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
         '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf']
    )
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    # .ttc fallback (macOS Helvetica)
    if os.path.exists('/System/Library/Fonts/Helvetica.ttc'):
        try:
            return ImageFont.truetype('/System/Library/Fonts/Helvetica.ttc', size, index=0)
        except Exception:
            pass
    return ImageFont.load_default()


def generate_weekly_calendar_image(monday_date):
    from PIL import Image, ImageDraw
    import random as _r

    GERMAN_MONTHS = ['JANUAR', 'FEBRUAR', 'MÄRZ', 'APRIL', 'MAI', 'JUNI',
                     'JULI', 'AUGUST', 'SEPTEMBER', 'OKTOBER', 'NOVEMBER', 'DEZEMBER']
    GERMAN_DAYS   = ['Mo.', 'Di.', 'Mi.', 'Do.', 'Fr.', 'Sa.', 'So.']

    sunday_date = monday_date + timedelta(days=6)

    # ── query ────────────────────────────────────────────────────────────────
    events = (Event.query
              .filter(Event.date >= datetime.combine(monday_date, time_type.min),
                      Event.date <= datetime.combine(sunday_date, time_type.max))
              .order_by(Event.date.asc())
              .all())

    by_day = defaultdict(list)
    for e in events:
        by_day[e.date.weekday()].append(e)

    # Parent-genre colours (one swatch per bucket, not per sub-genre)
    genre_color = {}
    for e in events:
        for p in parent_genres(e.genre):
            if p not in genre_color:
                genre_color[p] = _GENRE_PALETTE[len(genre_color) % len(_GENRE_PALETTE)]

    def event_primary_color(evt):
        for p in parent_genres(evt.genre):
            if p in genre_color:
                return genre_color[p]
        return (100, 100, 110)

    # Legend only shows parents that are actually the primary colour of an event
    legend_genres = {}
    for e in events:
        for p in parent_genres(e.genre):
            if p in genre_color and p not in legend_genres:
                legend_genres[p] = genre_color[p]
                break

    # ── design tokens ────────────────────────────────────────────────────────
    W, H      = 1080, 1350
    BG        = (12,  12,  13)
    RED       = (208, 20,  20)
    RED_DARK  = (158, 12,  12)
    WHITE     = (246, 243, 238)   # warm off-white
    LGRAY     = (182, 178, 172)   # warm light gray
    MGRAY     = ( 98,  95,  90)   # warm mid gray
    SEP_COL   = ( 72,  70,  76)
    MARGIN    = 36
    USABLE_W  = W - 2 * MARGIN

    # ── fonts ────────────────────────────────────────────────────────────────
    def load_black(size):
        for path in ['/Library/Fonts/Arial Black.ttf',
                     '/Library/Fonts/Arial Bold.ttf',
                     '/System/Library/Fonts/Supplemental/Arial Bold.ttf',
                     '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf']:
            if os.path.exists(path):
                try:
                    from PIL import ImageFont
                    return ImageFont.truetype(path, size)
                except Exception:
                    pass
        return _load_font(size, bold=True)

    f_hero    = load_black(108)
    f_datenum = load_black(82)
    f_month   = load_black(92)
    f_pill    = _load_font(23, bold=True)
    f_act     = _load_font(21, bold=True)
    f_venue   = _load_font(16)
    f_genre   = _load_font(17)
    f_footer  = _load_font(18, bold=True)

    # ── canvas ───────────────────────────────────────────────────────────────
    # Transparent background so Canva users can place their own background
    # layer beneath the content. No fill rectangle, no texture.
    img  = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # ── helpers ──────────────────────────────────────────────────────────────
    def wrap_text(text, font, max_w):
        """Word-wrap a single string; returns a list of lines."""
        if draw.textlength(text, font=font) <= max_w:
            return [text]
        words = text.split()
        lines, current = [], ''
        for word in words:
            test = (current + ' ' + word).strip()
            if draw.textlength(test, font=font) <= max_w:
                current = test
            else:
                if current:
                    lines.append(current)
                current = word
        if current:
            lines.append(current)
        return lines or [text]

    def rough_vline(x, y0, y1):
        y = y0
        while y < y1:
            dot = _r.randint(3, 7)
            gap = _r.randint(4, 10)
            jx  = _r.randint(-1, 1)
            w   = _r.choice([1, 1, 1, 2])
            draw.line([x + jx, y, x + jx, min(y + dot, y1)], fill=SEP_COL, width=w)
            y += dot + gap

    def rough_hline(x0, x1, y):
        x = x0
        while x < x1:
            dot = _r.randint(3, 7)
            gap = _r.randint(4, 10)
            jy  = _r.randint(-1, 1)
            w   = _r.choice([1, 1, 1, 2])
            draw.line([x, y + jy, min(x + dot, x1), y + jy], fill=SEP_COL, width=w)
            x += dot + gap

    def rough_hrule(y, color, thickness=7):
        """Solid horizontal rule with a slightly eaten top/bottom edge."""
        draw.rectangle([0, y, W, y + thickness], fill=color)
        # Chew into the edges randomly for a worn-print look
        for _ in range(W // 5):
            rx = _r.randint(0, W - 1)
            ry = _r.choice([y, y + thickness - 1])
            draw.point((rx, ry), fill=BG)

    def rough_pill(x0, y0, x1, y1, color):
        """Rounded rectangle pill with grain texture on the surface."""
        draw.rounded_rectangle([x0, y0, x1, y1], radius=5, fill=color)
        # Sparse darker dots for printed-ink texture
        for _ in range((x1 - x0) // 3):
            px = _r.randint(x0 + 2, x1 - 3)
            py = _r.randint(y0 + 1, y1 - 2)
            draw.point((px, py), fill=RED_DARK)

    # ── HEADER ───────────────────────────────────────────────────────────────
    rough_hrule(0, RED, thickness=7)

    ev_y = 14
    draw.text((MARGIN, ev_y), 'EVENTS', font=f_hero, fill=WHITE)

    dr = f"{monday_date.day}.-{sunday_date.day}."
    dr_w = draw.textlength(dr, font=f_datenum)
    draw.text((W - MARGIN - dr_w, ev_y + 14), dr, font=f_datenum, fill=WHITE)

    month_str = GERMAN_MONTHS[monday_date.month - 1]
    m_w = draw.textlength(month_str, font=f_month)
    draw.text((W - MARGIN - m_w, ev_y + 100), month_str, font=f_month, fill=RED)

    # ── genre legend (primary colours only) ──────────────────────────────────
    LEG_TOP = 240
    DOT_D   = 15
    ITEM_W  = USABLE_W // 3
    ROW_H   = 30

    for idx, (genre, color) in enumerate(legend_genres.items()):
        row_i = idx // 3
        col_i = idx % 3
        lx = MARGIN + col_i * ITEM_W
        ly = LEG_TOP + row_i * ROW_H
        if ly + DOT_D > LEG_TOP + 4 * ROW_H:
            break
        draw.ellipse([lx, ly, lx + DOT_D, ly + DOT_D], fill=color)
        tw_g  = ITEM_W - DOT_D - 10
        label = genre
        while label and draw.textlength(label, font=f_genre) > tw_g:
            label = label[:-1]
        draw.text((lx + DOT_D + 7, ly - 1), label, font=f_genre, fill=LGRAY)

    num_leg_rows = max(1, -(-len(legend_genres) // 3))  # ceiling div
    HDR_BOT = LEG_TOP + num_leg_rows * ROW_H + 18
    rough_hrule(HDR_BOT, RED, thickness=7)

    # ── CALENDAR GRID ─────────────────────────────────────────────────────────
    GRID_TOP = HDR_BOT + 7 + 18
    FOOTER_H = 44
    GRID_BOT = H - FOOTER_H

    ALL_GROUPS = [[0, 1, 2, 3], [4], [5], [6]]
    col_groups = [g for g in ALL_GROUPS if any(by_day.get(d) for d in g)]

    if not col_groups:
        msg = 'Keine Events diese Woche'
        mw  = draw.textlength(msg, font=f_act)
        draw.text(((W - mw) / 2, GRID_TOP + (GRID_BOT - GRID_TOP) // 2),
                  msg, font=f_act, fill=MGRAY)
    else:
        n_cols = len(col_groups)
        SEP_W  = 4
        col_w  = (USABLE_W - SEP_W * (n_cols - 1)) // n_cols

        for i in range(1, n_cols):
            sx = MARGIN + i * (col_w + SEP_W) - SEP_W
            rough_vline(sx, GRID_TOP, GRID_BOT)

        for col_i, day_group in enumerate(col_groups):
            cx = MARGIN + col_i * (col_w + SEP_W)
            cy = GRID_TOP
            tw = col_w - 28

            active_days = [d for d in day_group if by_day.get(d)]

            for day_i, weekday in enumerate(active_days):
                day_events = by_day[weekday]
                day_date   = monday_date + timedelta(days=weekday)

                if day_i > 0:
                    rough_hline(cx, cx + col_w, cy)
                    cy += 16

                # ── day pill ─────────────────────────────────────────────────
                pill_label = f"{GERMAN_DAYS[weekday]} {day_date.strftime('%d.%m.')}"
                pill_h     = 34
                pill_pad   = 12
                pill_w     = min(int(draw.textlength(pill_label, font=f_pill)) + pill_pad * 2,
                                 col_w - 4)
                rough_pill(cx + 2, cy, cx + 2 + pill_w, cy + pill_h, RED)
                draw.text((cx + 2 + pill_pad, cy + 6), pill_label,
                          font=f_pill, fill=WHITE)
                cy += pill_h + 10

                # ── events ───────────────────────────────────────────────────
                for evt in day_events:
                    if cy > GRID_BOT - 28:
                        draw.text((cx + 20, cy), '…', font=f_venue, fill=MGRAY)
                        break

                    color    = event_primary_color(evt)
                    DOT_D_EV = 14
                    draw.ellipse([cx + 4, cy + 4,
                                  cx + 4 + DOT_D_EV, cy + 4 + DOT_D_EV],
                                 fill=color)

                    tx = cx + DOT_D_EV + 12
                    acts_raw  = (evt.acts or evt.name or '').strip()
                    act_lines = [a.strip()
                                 for a in acts_raw.replace('\n', ',').split(',')
                                 if a.strip()] or [evt.name or '?']

                    for act in act_lines:
                        for line in wrap_text(act, f_act, tw):
                            if cy > GRID_BOT - 26:
                                break
                            draw.text((tx, cy), line, font=f_act, fill=WHITE)
                            cy += 25

                    if evt.venue and cy <= GRID_BOT - 20:
                        vstr = evt.venue.name
                        if evt.venue.city:
                            vstr += f', {evt.venue.city}'
                        for line in wrap_text(vstr, f_venue, tw)[:1]:
                            draw.text((tx, cy), line, font=f_venue, fill=MGRAY)
                        cy += 20

                    cy += 10

    # ── footer: year only, centred ───────────────────────────────────────────
    year_str = str(monday_date.year)
    yw = draw.textlength(year_str, font=f_footer)
    draw.text(((W - yw) / 2, H - FOOTER_H + 12), year_str,
              font=f_footer, fill=MGRAY)

    # ── film grain overlay ────────────────────────────────────────────────────
    light_dots = [(_r.randint(0, W - 1), _r.randint(0, H - 1)) for _ in range(16000)]
    draw.point(light_dots, fill=(32, 30, 35))
    dark_dots  = [(_r.randint(0, W - 1), _r.randint(0, H - 1)) for _ in range(8000)]
    draw.point(dark_dots, fill=(4, 4, 5))

    return img


def _fmt_duration(td: timedelta) -> str:
    total = int(abs(td.total_seconds()))
    if total < 60:
        return f"{total}s"
    if total < 3600:
        return f"{total // 60}m"
    hours = total // 3600
    minutes = (total % 3600) // 60
    return f"{hours}h {minutes}m" if minutes else f"{hours}h"


@app.route('/admin', methods=['GET'])
@admin_required
def admin():
    events = Event.query.order_by(Event.date.asc()).all()
    delete_form = DeleteEventForm()
    last_scrape = _get_last_scrape_time()
    now = datetime.now()
    next_scrape = (last_scrape + timedelta(hours=SCRAPE_INTERVAL_HOURS)) if last_scrape else None
    time_since_last = _fmt_duration(now - last_scrape) + " ago" if last_scrape else "never"
    time_until_next = _fmt_duration(next_scrape - now) if next_scrape and next_scrape > now else "soon"
    today = now.date()
    current_monday = (today - timedelta(days=today.weekday())).isoformat()
    return render_template(
        'admin.html', events=events, delete_form=delete_form,
        last_scrape=last_scrape, next_scrape=next_scrape,
        time_since_last=time_since_last, time_until_next=time_until_next,
        scrape_running=_scrape_running, current_monday=current_monday,
    )

@app.route('/edit_event/<int:event_id>', methods=['GET', 'POST'])
@admin_required
def edit_event(event_id):
    event = Event.query.get_or_404(event_id)
    form = EventEditForm(obj=event)
    if request.method == 'GET':
        form.venue_id.data = str(event.venue_id)
        form.venue_name.data = event.venue.name
        form.venue_address.data = event.venue.address
        form.venue_city.data = event.venue.city
        form.venue_canton.data = event.venue.canton
        form.venue_plz.data = event.venue.plz
        form.venue_coords.data = event.venue.coords or ''
        form.genre.data = [g.strip() for g in (event.genre or '').split(',') if g.strip()]
        form.end_date.data = event.end_date
        form.is_festival.data = event.is_festival
    if request.method == 'POST':
        if form.validate_on_submit():
            event.name = form.name.data
            event.date = form.date.data
            event.end_date = form.end_date.data
            event.is_festival = form.is_festival.data
            event.doors = form.doors.data
            event.acts = form.acts.data
            event.ticket_price = form.ticket_price.data
            event.ticket_link = form.ticket_link.data
            event.genre = ', '.join(form.genre.data) if form.genre.data else ''

            venue_id = form.venue_id.data
            if venue_id and venue_id != 'new':
                venue = Venue.query.get(venue_id)
                if not venue:
                    flash('Selected venue does not exist.')
                    return redirect(url_for('edit_event', event_id=event_id))
            else:
                venue = Venue.query.filter_by(
                    name=form.venue_name.data, city=form.venue_city.data, plz=form.venue_plz.data
                ).first()
                if not venue:
                    venue = Venue(
                        name=form.venue_name.data,
                        address=form.venue_address.data,
                        city=form.venue_city.data,
                        canton=form.venue_canton.data,
                        plz=form.venue_plz.data,
                        coords=form.venue_coords.data
                    )
                    db.session.add(venue)
                    db.session.flush()
            event.venue_id = venue.id

            # Handle flyer upload
            if form.flyer.data:
                file = form.flyer.data
                if file and allowed_file(file.filename) and validate_image_content(file):
                    filename = secure_filename(file.filename)
                    flyer = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                    file.save(flyer)
                    event.flyer = flyer

            db.session.commit()
            flash('Event updated successfully!')
            return redirect(url_for('admin'))

    return render_template('edit_event.html', form=form, event=event)

@app.route('/delete_event/<int:event_id>', methods=['POST'])
@admin_required
def delete_event(event_id):
    form = DeleteEventForm()
    if not form.validate_on_submit():
        abort(400)
    event = Event.query.get_or_404(event_id)
    db.session.delete(event)
    db.session.commit()
    flash('Event deleted successfully!')
    return redirect(url_for('admin'))


@app.route('/admin/scrape-status')
@admin_required
def scrape_status():
    if not _scrape_running:
        return jsonify({'running': False})
    progress = dict(_scrape_progress)
    total = progress.get('total', 0)
    processed = progress.get('processed', 0)
    started_at = progress.get('started_at')
    eta_seconds = None
    if started_at and processed > 0 and total > 0:
        elapsed = (datetime.now() - datetime.fromisoformat(started_at)).total_seconds()
        rate = elapsed / processed
        remaining = total - processed
        eta_seconds = int(rate * remaining)
    return jsonify({
        'running': True,
        'phase': progress.get('phase', ''),
        'total': total,
        'processed': processed,
        'eta_seconds': eta_seconds,
    })


@app.route('/admin/export-excel')
@admin_required
def export_excel():
    import openpyxl
    from openpyxl.styles import Font

    events = Event.query.order_by(Event.date.asc()).all()
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Events'

    headers = ['Date', 'Name', 'Acts', 'Genre', 'Venue', 'City', 'Canton',
               'Doors', 'Ticket Price', 'Ticket Link', 'Source URL']
    bold = Font(bold=True)

    current_date = None
    row_num = 1

    for event in events:
        event_date = event.date.date() if event.date else None
        if event_date != current_date:
            if current_date is not None:
                row_num += 1  # blank row between date groups
            # Write date header
            cell = ws.cell(row=row_num, column=1, value=event_date.strftime('%A, %d %B %Y') if event_date else 'Unknown')
            cell.font = bold
            row_num += 1
            # Write column headers
            for col, h in enumerate(headers, 1):
                cell = ws.cell(row=row_num, column=col, value=h)
                cell.font = bold
            row_num += 1
            current_date = event_date

        venue = event.venue
        ws.cell(row=row_num, column=1, value=event_date.strftime('%Y-%m-%d') if event_date else '')
        ws.cell(row=row_num, column=2, value=event.name)
        ws.cell(row=row_num, column=3, value=event.acts)
        ws.cell(row=row_num, column=4, value=event.genre)
        ws.cell(row=row_num, column=5, value=venue.name if venue else '')
        ws.cell(row=row_num, column=6, value=venue.city if venue else '')
        ws.cell(row=row_num, column=7, value=venue.canton if venue else '')
        ws.cell(row=row_num, column=8, value=event.doors.strftime('%H:%M') if event.doors else '')
        ws.cell(row=row_num, column=9, value=event.ticket_price)
        ws.cell(row=row_num, column=10, value=event.ticket_link)
        ws.cell(row=row_num, column=11, value=event.source_url)
        row_num += 1

    # Auto-size columns
    for col in ws.columns:
        max_len = 0
        col_letter = col[0].column_letter
        for cell in col:
            if cell.value:
                max_len = max(max_len, len(str(cell.value)))
        ws.column_dimensions[col_letter].width = min(max_len + 2, 50)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(
        buf,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=f'events_{datetime.now().strftime("%Y%m%d")}.xlsx',
    )


@app.route('/admin/weekly-image')
@admin_required
def weekly_calendar_image():
    week_param = request.args.get('week', '')
    try:
        ref = datetime.strptime(week_param, '%Y-%m-%d').date()
    except ValueError:
        ref = datetime.now().date()
    monday = ref - timedelta(days=ref.weekday())

    img = generate_weekly_calendar_image(monday)
    buf = io.BytesIO()
    img.save(buf, format='PNG', optimize=True)
    buf.seek(0)
    return send_file(
        buf,
        mimetype='image/png',
        as_attachment=True,
        download_name=f'events_week_{monday.strftime("%Y-%m-%d")}.png',
    )


@app.route('/')
def calendar_view():
    now = datetime.now()

    # Calculate the current month and the next two months
    months = []
    for i in range(3):
        month_date = now + relativedelta(months=i)
        months.append((month_date.year, month_date.month))

    # Define the start and end dates for querying events
    start_date = datetime(months[0][0], months[0][1], 1)
    end_month = months[-1]
    last_day = calendar.monthrange(end_month[0], end_month[1])[1]
    end_date = datetime(end_month[0], end_month[1], last_day, 23, 59, 59)

    # Query events within the date range
    events = Event.query.filter(Event.date >= start_date, Event.date <= end_date).order_by(Event.date.asc()).all()

    # Group events by their date
    grouped_events = defaultdict(list)
    for event in events:
        event_date = event.date.date()
        grouped_events[event_date].append(event)

    # Prepare month data for the template
    months_data = []
    for year, month in months:
        last_day_of_month = calendar.monthrange(year, month)[1]
        months_data.append({
            'year': year,
            'month': month,
            'last_day_of_month': last_day_of_month
        })

    return render_template('calendar.html', grouped_events=grouped_events, months_data=months_data, datetime=datetime)

@app.route('/get_venues')
def get_venues():
    venues = Venue.query.order_by(Venue.name.asc()).all()
    venue_list = []
    for venue in venues:
        venue_list.append({
            'id': venue.id,
            'name': venue.name,
            'address': venue.address,
            'city': venue.city,
            'plz': venue.plz,
            'canton': venue.canton,
            'coords': venue.coords if venue.coords else 'N/A'
        })
    return jsonify({'venues': venue_list})


@app.route('/events/<int:event_id>/')
def event_page(event_id):
    event = Event.query.get_or_404(event_id)
    return render_template('')

def parse_scraped_events(date_from=None, date_to=None, source=None):
    """Query the ScrapedEvent table and filter events.

    Returns only unapproved events, sorted by date ascending.  Optional
    filter parameters narrow the result set further.

    Args:
        date_from: Earliest start_date to include (date object, default: today).
        date_to:   Latest start_date to include (date object, default: today + 2 months).
        source:    If set, only include events from this source string.
    """
    now = datetime.now().date()
    if date_from is None:
        date_from = now
    if date_to is None:
        date_to = now + relativedelta(months=2)
    # Query all unapproved scraped events within the date window
    q = ScrapedEvent.query.filter(
        ScrapedEvent.approved.is_(False),
        ScrapedEvent.start_date >= date_from,
        ScrapedEvent.start_date <= date_to,
    )
    if source:
        q = q.filter(ScrapedEvent.source == source)
    candidates = q.order_by(ScrapedEvent.start_date.asc()).all()
    events = []
    for rec in candidates:
        # Filter by styles containing 'concert' (MetalGigs uses genre tags instead)
        styles = (rec.styles or '').lower()
        if rec.source != 'metalgigs' and 'concert' not in styles:
            continue
        # convert to dictionary for template
        data = {
            'source': rec.source,
            'url': rec.url,
            'title': rec.title,
            'performers': rec.performers if rec.performers else rec.title,
            'styles': rec.styles,
            'description': rec.description,
            'start_date': rec.start_date.isoformat() if rec.start_date else None,
            'end_date': rec.end_date.isoformat() if rec.end_date else None,
            'doors_open': rec.doors_open.strftime('%H:%M') if rec.doors_open else None,
            'start_time': rec.start_time.strftime('%H:%M') if rec.start_time else None,
            'venue_name': rec.venue_name,
            'street_address': rec.street_address,
            'city': rec.city,
            'region': rec.region,
            'postal_code': rec.postal_code,
            'ticket_price': rec.ticket_price,
            'ticket_currency': rec.ticket_currency,
            'ticket_url': rec.ticket_url,
            'organizer': rec.organizer,
            'event_status': rec.event_status,
        }
        # Add derived event_date for sorting/comparison
        data['_event_date'] = datetime.combine(rec.start_date, datetime.min.time()) if rec.start_date else None
        # Store primary key so we can mark as approved later
        data['_scraped_id'] = rec.id
        events.append(data)
    return events

if __name__ == '__main__':
    app.run(debug=True, port=5001)
