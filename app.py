import calendar
import functools
import hashlib
import io
import os
import threading
from collections import defaultdict
from datetime import datetime, time as time_type, timedelta
from dateutil.relativedelta import relativedelta

from dotenv import load_dotenv
from flask import Flask, render_template, redirect, url_for, flash, request, jsonify, abort, session, send_file
from werkzeug.utils import secure_filename

from forms import EventForm, EventEditForm, LoginForm, DeleteEventForm, SetPasswordForm
from models import db, Event, Venue, Submitter, ScrapedEvent
from utils import resolve_canton

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
    skip = {'concert', 'konzert', 'live'}
    parts = [p.strip() for p in raw.replace('·', ',').split(',')]
    return ', '.join(p for p in parts if p and p.lower() not in skip)


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


def _scrape_and_import():
    """Run scrapers and insert results directly into the ScrapedEvent table."""
    global _scrape_running, _scrape_progress
    try:
        scraper = _load_scraper()
        get_sitemap_event_urls = scraper.get_sitemap_event_urls
        get_petzi_event_urls = scraper.get_petzi_event_urls
        parse_metalgigs_event = scraper.parse_metalgigs_event
        parse_petzi_event = scraper.parse_petzi_event
        from scripts.import_scraped_events import parse_date, parse_time

        _scrape_progress = {'total': 0, 'processed': 0, 'started_at': datetime.now().isoformat(), 'phase': 'Fetching sitemaps'}

        events = []
        mg_urls = get_sitemap_event_urls("https://metalgigs.ch/sitemap.xml", "/konzerte/")
        petzi_urls = get_sitemap_event_urls("https://www.petzi.ch/en/sitemap.xml", "/en/events/")
        if not petzi_urls:
            petzi_urls = get_petzi_event_urls()

        all_urls = [('metalgigs', url, parse_metalgigs_event) for url in mg_urls] + \
                   [('petzi', url, parse_petzi_event) for url in petzi_urls]
        _scrape_progress['total'] = len(all_urls)
        _scrape_progress['phase'] = 'Scraping events'

        for _source, url, parser in all_urls:
            parsed = parser(url)
            if parsed:
                events.append(parsed)
            _scrape_progress['processed'] += 1

        _scrape_progress['phase'] = 'Importing to database'
        with app.app_context():
            count = 0
            for row in events:
                source = row.get('source')
                url = row.get('url')
                existing = ScrapedEvent.query.filter_by(source=source, url=url).first()
                if existing:
                    continue
                region = resolve_canton(row.get('region') or '', row.get('city') or '')
                scraped = ScrapedEvent(
                    source=source,
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


@app.route('/about', methods=['GET'])
def about():
    return render_template('about.html')


@app.route('/queue', methods=['GET', 'POST'])
@login_required
def event_queue():
    """Display and approve scraped events for a given submitter.

    GET: show the list of filtered events with approve buttons.
    POST: create the selected event in the database.
    """
    submitter = db.session.get(Submitter, session['user_id'])
    # Load events from the database via the ScrapedEvent model
    events = parse_scraped_events()
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
        return redirect(url_for('event_queue'))
    return render_template('event_queue.html', events=events)

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

@app.route('/admin', methods=['GET'])
@admin_required
def admin():
    events = Event.query.order_by(Event.date.asc()).all()
    delete_form = DeleteEventForm()
    last_scrape = _get_last_scrape_time()
    scrape_cooldown = False
    if last_scrape and (datetime.now() - last_scrape) < timedelta(hours=24):
        scrape_cooldown = True
    return render_template(
        'admin.html', events=events, delete_form=delete_form,
        last_scrape=last_scrape, scrape_cooldown=scrape_cooldown,
        scrape_running=_scrape_running,
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
    if request.method == 'POST':
        if form.validate_on_submit():
            event.name = form.name.data
            event.date = form.date.data
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

@app.route('/admin/trigger-scrape', methods=['POST'])
@admin_required
def trigger_scrape():
    global _scrape_running
    form = DeleteEventForm()
    if not form.validate_on_submit():
        abort(400)
    with _scrape_lock:
        if _scrape_running:
            flash('A scrape is already running.')
            return redirect(url_for('admin'))
        last_scrape = _get_last_scrape_time()
        if last_scrape and (datetime.now() - last_scrape) < timedelta(hours=24):
            flash('Scrape cooldown active. Try again later.')
            return redirect(url_for('admin'))
        _scrape_running = True
    thread = threading.Thread(target=_scrape_and_import, daemon=True)
    thread.start()
    flash('Scrape started in the background. New events will appear in the queue.')
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
    venues = Venue.query.all()
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

def parse_scraped_events():
    """Query the ScrapedEvent table and filter events.

    Returns only events that are unapproved, tagged as concerts and
    occurring within the next two months.  Each returned record is
    converted into a dictionary with an extra `_event_date` key for
    compatibility with the approval logic.
    """
    now = datetime.now().date()
    cutoff_date = now + relativedelta(months=2)
    # Query all unapproved scraped events within the date window
    candidates = ScrapedEvent.query.filter(
        ScrapedEvent.approved.is_(False),
        ScrapedEvent.start_date <= cutoff_date,
        ScrapedEvent.start_date >= now
    ).all()
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
