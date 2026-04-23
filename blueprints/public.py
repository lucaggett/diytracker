import calendar
from collections import defaultdict
from datetime import datetime

from dateutil.relativedelta import relativedelta
from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, session, url_for

from forms import AccessibilityForm, CollaboratorRequestForm
from models import Event, Venue, VenueAccessibility, db
from services.contact import build_contact_logger, send_contact_email
from services.i18n import SUPPORTED_LOCALES, gettext as _, validate_lang

bp = Blueprint('public', __name__)

_contact_logger = None


def _get_contact_logger():
    global _contact_logger
    if _contact_logger is None:
        _contact_logger = build_contact_logger()
    return _contact_logger


@bp.route('/')
def calendar_view():
    now = datetime.now()

    months = []
    for i in range(3):
        month_date = now + relativedelta(months=i)
        months.append((month_date.year, month_date.month))

    start_date = datetime(months[0][0], months[0][1], 1)
    end_month = months[-1]
    last_day = calendar.monthrange(end_month[0], end_month[1])[1]
    end_date = datetime(end_month[0], end_month[1], last_day, 23, 59, 59)

    events = Event.query.filter(Event.date >= start_date, Event.date <= end_date).order_by(Event.date.asc()).all()

    grouped_events = defaultdict(list)
    for event in events:
        event_date = event.date.date()
        grouped_events[event_date].append(event)

    months_data = []
    for year, month in months:
        last_day_of_month = calendar.monthrange(year, month)[1]
        months_data.append({
            'year': year,
            'month': month,
            'last_day_of_month': last_day_of_month,
        })

    return render_template('calendar.html', grouped_events=grouped_events, months_data=months_data, datetime=datetime)


@bp.route('/about', methods=['GET', 'POST'])
def about():
    form = CollaboratorRequestForm()
    if form.validate_on_submit():
        name = form.name.data.strip()
        sender_email = form.email.data.strip()
        message = form.message.data.strip()
        honeypot_filled = bool((form.website.data or '').strip())
        single_line_message = message.replace('\n', ' \\n ')
        logger = _get_contact_logger()
        if honeypot_filled:
            logger.info('honeypot name=%r email=%r message=%r',
                        name, sender_email, single_line_message)
        else:
            try:
                send_contact_email(name, sender_email, message)
            except Exception as exc:
                logger.exception('send_failed name=%r email=%r message=%r error=%s',
                                 name, sender_email, single_line_message, exc)
                current_app.logger.exception('Failed to send collaborator email')
                flash(_('Your message could not be sent — please write to us directly at kontakt@diytracker.ch.'))
                return redirect(url_for('public.about'))
            logger.info('sent name=%r email=%r message=%r',
                        name, sender_email, single_line_message)
        flash(_('Thanks! We will get back to you as soon as possible.'))
        return redirect(url_for('public.about'))
    return render_template('about.html', form=form)


@bp.route('/<lang>/impressum')
def impressum(lang):
    validate_lang(lang)
    return render_template('impressum.html')


@bp.route('/<lang>/agb')
def agb(lang):
    validate_lang(lang)
    return render_template('agb.html')


@bp.route('/<lang>/datenschutz')
def datenschutz(lang):
    validate_lang(lang)
    return render_template('datenschutz.html')


@bp.route('/accessibility/<token>', methods=['GET', 'POST'])
def accessibility_form(token):
    venue = Venue.query.filter_by(accessibility_token=token).first_or_404()
    info = venue.accessibility
    if info is None:
        info = VenueAccessibility(venue_id=venue.id)
    form = AccessibilityForm(obj=info)
    if form.validate_on_submit():
        form.populate_obj(info)
        info.venue_id = venue.id
        info.updated_at = datetime.utcnow()
        db.session.add(info)
        db.session.commit()
        flash(_('Accessibility info saved — thank you!'))
        return redirect(url_for('public.accessibility_form', token=token))
    return render_template('accessibility_form.html', form=form, venue=venue, info=info)


@bp.route('/venues/<int:venue_id>/accessibility')
def venue_accessibility(venue_id):
    venue = Venue.query.get_or_404(venue_id)
    return render_template('venue_accessibility.html', venue=venue, info=venue.accessibility)


@bp.route('/set-language/<lang>')
def set_language(lang):
    if lang not in SUPPORTED_LOCALES:
        abort(404)
    session['lang'] = lang
    next_url = request.args.get('next', '')
    if next_url.startswith('/') and not next_url.startswith('//'):
        return redirect(next_url)
    return redirect(url_for('public.calendar_view'))
