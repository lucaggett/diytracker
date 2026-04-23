import hashlib
import os
from datetime import datetime, time as time_type

from dateutil.relativedelta import relativedelta
from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from werkzeug.utils import secure_filename

from forms import EventForm
from models import db, Event, ScrapedEvent, Submitter, Venue
from services.auth import login_required
from services.i18n import gettext as _
from services.uploads import UPLOAD_FOLDER, allowed_file, validate_image_content
from utils import clean_genre_tokens, resolve_canton

bp = Blueprint('submissions', __name__)


def _clean_genre(raw):
    return ', '.join(clean_genre_tokens(raw))


def parse_scraped_events(date_from=None, date_to=None, source=None):
    """Query the ScrapedEvent table and filter events."""
    now = datetime.now().date()
    if date_from is None:
        date_from = now
    if date_to is None:
        date_to = now + relativedelta(months=2)
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
        data['_event_date'] = datetime.combine(rec.start_date, datetime.min.time()) if rec.start_date else None
        data['_scraped_id'] = rec.id
        events.append(data)
    return events


@bp.route('/queue', methods=['GET', 'POST'])
@login_required
def event_queue():
    submitter = db.session.get(Submitter, session['user_id'])

    def _parse_date(key):
        raw = request.args.get(key, '').strip()
        try:
            return datetime.strptime(raw, '%Y-%m-%d').date() if raw else None
        except ValueError:
            return None

    filter_date_from = _parse_date('date_from')
    filter_date_to = _parse_date('date_to')
    filter_source = request.args.get('source', '').strip() or None
    events = parse_scraped_events(
        date_from=filter_date_from,
        date_to=filter_date_to,
        source=filter_source,
    )
    if request.method == 'POST':
        try:
            idx = int(request.form.get('index'))
            data = events[idx]
        except (ValueError, IndexError):
            flash(_('Invalid event selection.'))
            return redirect(url_for('submissions.event_queue'))

        def _ov(key, fallback):
            val = request.form.get(key, '').strip()
            return val if val else fallback

        name = _ov('override_name', data.get('title') or data.get('performers') or 'Concert')
        venue_name = _ov('override_venue_name', data.get('venue_name') or 'Unknown venue')
        city = _ov('override_city', data.get('city') or '')
        plz = _ov('override_postal_code', data.get('postal_code') or '')
        street = _ov('override_street_address', data.get('street_address'))
        raw_genre = _ov('override_genre', data.get('styles') or '')
        genre = _clean_genre(raw_genre)
        acts = _ov('override_acts', data.get('performers') or '')
        ticket_price = _ov('override_ticket_price', data.get('ticket_price') or '')
        ticket_link = _ov('override_ticket_url', data.get('ticket_url') or data.get('ticket_link') or '')
        description = _ov('override_description', data.get('description') or '')
        source_url = _ov('override_source_url', data.get('url') or '')

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

        venue = Venue.query.filter_by(name=venue_name, city=city, plz=plz).first()
        if not venue:
            venue = Venue(
                name=venue_name,
                address=street,
                city=city,
                canton=resolve_canton(data.get('region') or '', city),
                plz=plz,
                coords=data.get('coords') or '',
            )
            db.session.add(venue)
            db.session.commit()
        event_hash = hashlib.sha256(
            f"{name}{event_date}{doors_time}{genre}{acts}{ticket_link}{ticket_price}{venue.id}".encode()
        ).hexdigest()
        existing = Event.query.filter_by(event_hash=event_hash).first()
        if existing:
            flash(_('This event already exists.'))
            return redirect(url_for('submissions.event_queue'))
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
            submitter_id=submitter.id,
        )
        db.session.add(new_event)
        db.session.commit()
        scraped_id = data.get('_scraped_id')
        if scraped_id:
            scraped_obj = ScrapedEvent.query.get(scraped_id)
            if scraped_obj:
                scraped_obj.approved = True
                scraped_obj.approved_at = datetime.now()
                scraped_obj.approved_event_id = new_event.id
                db.session.commit()
        flash(_('Event approved and added to calendar!'))
        redirect_args = {k: v for k, v in request.args.items()}
        return redirect(url_for('submissions.event_queue', **redirect_args))
    now = datetime.now().date()
    return render_template(
        'event_queue.html',
        events=events,
        filter_date_from=(filter_date_from or now).isoformat(),
        filter_date_to=(filter_date_to or (now + relativedelta(months=2))).isoformat(),
        filter_source=filter_source or '',
    )


@bp.route('/submit', methods=['GET', 'POST'])
@login_required
def submit_event_link():
    form = EventForm()
    submitter = db.session.get(Submitter, session['user_id'])

    if form.validate_on_submit():
        name = form.name.data
        date = form.date.data
        end_date = form.end_date.data
        is_festival = form.is_festival.data
        doors = form.doors.data
        genres = form.genre.data
        acts = form.acts.data
        ticket_link = form.ticket_link.data
        ticket_price = form.ticket_price.data

        flyer = None
        if form.flyer.data:
            file = form.flyer.data
            if file and allowed_file(file.filename) and validate_image_content(file):
                filename = secure_filename(file.filename)
                flyer_path = os.path.join(current_app.config.get('UPLOAD_FOLDER', UPLOAD_FOLDER), filename)
                file.save(flyer_path)
                flyer = flyer_path

        venue_id = form.venue_id.data
        if venue_id == 'new' or not venue_id:
            venue_name = form.venue_name.data
            venue_address = form.venue_address.data
            venue_city = form.venue_city.data
            venue_canton = form.venue_canton.data
            venue_plz = form.venue_plz.data
            venue_coords = form.venue_coords.data

            if not venue_name or not venue_city or not venue_plz:
                flash(_('Please provide all required venue details for a new venue.'))
                return redirect(url_for('submissions.submit_event_link'))

            venue = Venue.query.filter_by(
                name=venue_name,
                city=venue_city,
                plz=venue_plz,
            ).first()

            if not venue:
                venue = Venue(
                    name=venue_name,
                    address=venue_address,
                    city=venue_city,
                    canton=venue_canton,
                    plz=venue_plz,
                    coords=venue_coords,
                )
                db.session.add(venue)
                db.session.commit()
            else:
                flash(_('Venue already exists. Using existing venue.'))
        else:
            venue = Venue.query.get(venue_id)
            if not venue:
                flash(_('Selected venue does not exist.'))
                return redirect(url_for('submissions.submit_event_link'))

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
            submitter_id=submitter.id,
        )
        db.session.add(new_event)
        db.session.commit()

        flash(_('Event submitted successfully!'))
        return redirect(url_for('public.calendar_view'))

    return render_template('submit_event.html', form=form)


@bp.route('/events/<int:event_id>/')
def event_page(event_id):
    event = Event.query.get_or_404(event_id)
    return render_template('')
