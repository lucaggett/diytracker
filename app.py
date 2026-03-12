import calendar
import os
from collections import defaultdict
from datetime import datetime, time as time_type
from dateutil.relativedelta import relativedelta

from flask import Flask, render_template, redirect, url_for, flash, request, jsonify, abort
from werkzeug.utils import secure_filename

from forms import EventForm, EventEditForm
from models import db, Event, Venue, Submitter, ScrapedEvent
from utils import resolve_canton

# Check that required directories exist
if not os.path.exists('logs'):
    os.makedirs('logs')

if not os.path.exists('static/uploads'):
    os.makedirs('static/uploads')

app = Flask(__name__)

app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///events.db'  # SQLite for simplicity
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = 'your_secret_key'

db.init_app(app)

with app.app_context():
    db.create_all()

app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['ALLOWED_EXTENSIONS'] = {'png', 'jpg', 'jpeg', 'gif'}

# Check allowed file extensions
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']


def _clean_genre(raw: str) -> str:
    skip = {'concert', 'konzert', 'live'}
    parts = [p.strip() for p in raw.replace('·', ',').split(',')]
    return ', '.join(p for p in parts if p and p.lower() not in skip)


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


@app.route('/queue/<string:submission_code>', methods=['GET', 'POST'])
def event_queue(submission_code):
    """Display and approve scraped events for a given submitter.

    GET: show the list of filtered events with approve buttons.
    POST: create the selected event in the database.
    """
    submitter = Submitter.query.filter_by(submission_code=submission_code).first()
    if not submitter:
        abort(404)
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
            return redirect(url_for('event_queue', submission_code=submission_code))
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
        event_hash = hash(f"{name}{event_date}{doors_time}{genre}{acts}{ticket_link}{ticket_price}{venue.id}")
        # Check if event already exists
        existing = Event.query.filter_by(event_hash=str(event_hash)).first()
        if existing:
            flash('This event already exists.')
            return redirect(url_for('event_queue', submission_code=submission_code))
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
            event_hash=str(event_hash),
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
        return redirect(url_for('event_queue', submission_code=submission_code))
    return render_template('event_queue.html', events=events)

@app.route('/submit/<string:submission_code>', methods=['GET', 'POST'])
def submit_event_link(submission_code):
    """
    Allows event submission via a unique link associated with each user.
    """
    form = EventForm()

    # Look up the submitter by the submission code
    submitter = Submitter.query.filter_by(submission_code=submission_code).first()
    if not submitter:
        flash("Invalid or expired submission link.")
        return redirect(url_for('calendar_view'))

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
            if file and allowed_file(file.filename):
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
                return redirect(url_for('submit_event_link', submission_code=submission_code))

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
                return redirect(url_for('submit_event_link', submission_code=submission_code))

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
            event_hash=hash(f"{name}{date}{doors}{genres}{acts}{ticket_link}{ticket_price}{venue.id}"),
            # Assign the submitter so we know who created it:
            submitter_id=submitter.id
        )
        db.session.add(new_event)
        db.session.commit()

        flash('Event submitted successfully!')
        return redirect(url_for('calendar_view'))

    return render_template('submit_event.html', form=form)

@app.route('/admin', methods=['GET'])
def admin():
    events = Event.query.order_by(Event.date.asc()).all()
    return render_template('admin.html', events=events)

@app.route('/edit_event/<int:event_id>', methods=['GET', 'POST'])
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
            password = form.password.data
            if password != open('ADMIN_PASSWORD').read().strip():
                flash('Invalid password')
                return redirect(url_for('edit_event', event_id=event_id))

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
                if file and allowed_file(file.filename):
                    filename = secure_filename(file.filename)
                    flyer = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                    file.save(flyer)
                    event.flyer = flyer

            db.session.commit()
            flash('Event updated successfully!')
            return redirect(url_for('admin'))

    return render_template('edit_event.html', form=form, event=event)

@app.route('/delete_event/<int:event_id>', methods=['POST'])
def delete_event(event_id):
    event = Event.query.get_or_404(event_id)
    password = request.form.get('password')
    if password != open('ADMIN_PASSWORD').read().strip():
        flash('Invalid password')
        return redirect(url_for('admin'))

    db.session.delete(event)
    db.session.commit()
    flash('Event deleted successfully!')
    return redirect(url_for('admin'))

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