from datetime import datetime, time as time_type

from dateutil.relativedelta import relativedelta
from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from diytracker.forms import DeleteScrapedEventForm, EventForm, GenreForm
from diytracker.models import db, Genre, ScrapedEvent, Submitter
from diytracker.services.auth import admin_required, login_required
from diytracker.services.cache import bust_cache
from diytracker.services.events import (
    clean_genre_string,
    create_event,
    resolve_venue_from_form,
)
from diytracker.services.genre_catalog import find_genre
from diytracker.services.i18n import gettext as _
from diytracker.services.ingest import parse_time
from diytracker.services.labels import all_label_choices
from diytracker.services.uploads import UPLOAD_FOLDER, save_flyer_file
from diytracker.services.venue import get_or_create_venue
from diytracker.utils import is_safe_link, resolve_canton

bp = Blueprint("submissions", __name__)

_ALLOWED_QUEUE_PARAMS = {"date_from", "date_to", "source"}


def _queue_redirect_args():
    """Preserve the queue's filter params across a POST redirect."""
    return {k: v for k, v in request.args.items() if k in _ALLOWED_QUEUE_PARAMS}


def _scraped_event_to_dict(rec):
    data = {
        "source": rec.source,
        "url": rec.url,
        "title": rec.title,
        "performers": rec.performers if rec.performers else rec.title,
        "styles": rec.styles,
        "description": rec.description,
        "start_date": rec.start_date.isoformat() if rec.start_date else None,
        "end_date": rec.end_date.isoformat() if rec.end_date else None,
        "doors_open": rec.doors_open.strftime("%H:%M") if rec.doors_open else None,
        "start_time": rec.start_time.strftime("%H:%M") if rec.start_time else None,
        "venue_name": rec.venue_name,
        "street_address": rec.street_address,
        "city": rec.city,
        "region": rec.region,
        "postal_code": rec.postal_code,
        "ticket_price": rec.ticket_price,
        "ticket_currency": rec.ticket_currency,
        "ticket_url": rec.ticket_url,
        "organizer": rec.organizer,
        "event_status": rec.event_status,
        "flyer": rec.flyer,
        "submitter": rec.submitter,
        "needs_review": rec.needs_review,
        "review_reason": rec.review_reason,
    }
    data["_event_date"] = (
        datetime.combine(rec.start_date, datetime.min.time())
        if rec.start_date
        else None
    )
    data["_scraped_id"] = rec.id
    return data


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
    # Possible duplicates (needs_review) surface first so they get looked at.
    candidates = q.order_by(
        ScrapedEvent.needs_review.desc(), ScrapedEvent.start_date.asc()
    ).all()
    return [_scraped_event_to_dict(rec) for rec in candidates]


# Admin-only: approving a queue entry publishes it to the live calendar, and
# deleting one is permanent. An invite grants /submit, not moderation.
@bp.route("/queue", methods=["GET", "POST"])
@admin_required
def event_queue():
    submitter = db.session.get(Submitter, session["user_id"])

    def _parse_date(key):
        raw = request.args.get(key, "").strip()
        try:
            return datetime.strptime(raw, "%Y-%m-%d").date() if raw else None
        except ValueError:
            return None

    filter_date_from = _parse_date("date_from")
    filter_date_to = _parse_date("date_to")
    filter_source = request.args.get("source", "").strip() or None
    events = parse_scraped_events(
        date_from=filter_date_from,
        date_to=filter_date_to,
        source=filter_source,
    )
    if request.method == "POST":
        try:
            scraped_id = int(request.form.get("scraped_id"))
        except (TypeError, ValueError):
            scraped_id = None
        rec = db.session.get(ScrapedEvent, scraped_id) if scraped_id else None
        if rec is None or rec.approved:
            flash(_("Invalid event selection."))
            return redirect(url_for("submissions.event_queue"))
        data = _scraped_event_to_dict(rec)

        def _ov(key, fallback):
            val = request.form.get(key, "").strip()
            return val if val else fallback

        name = _ov(
            "override_name", data.get("title") or data.get("performers") or "Concert"
        )
        venue_name = _ov(
            "override_venue_name", data.get("venue_name") or "Unknown venue"
        )
        city = _ov("override_city", data.get("city") or "")
        plz = _ov("override_postal_code", data.get("postal_code") or "")
        street = _ov("override_street_address", data.get("street_address"))
        raw_genre = _ov("override_genre", data.get("styles") or "")
        genre = clean_genre_string(raw_genre)
        acts = _ov("override_acts", data.get("performers") or "")
        ticket_price = _ov("override_ticket_price", data.get("ticket_price") or "")
        # This path never goes through EventForm, so the href scheme check
        # that SafeLink does there has to happen here too — the override
        # field is free text typed into the queue.
        ticket_link = _ov(
            "override_ticket_url",
            data.get("ticket_url") or data.get("ticket_link") or "",
        )
        if not is_safe_link(ticket_link):
            ticket_link = ""
        description = _ov("override_description", data.get("description") or "")
        source_url = _ov("override_source_url", data.get("url") or "")

        override_date = request.form.get("override_date", "").strip()
        event_date = data.get("_event_date")
        if override_date:
            try:
                # Kept a datetime (not ingest.parse_date's date): Event.date
                # is a DateTime column and the dedup hash stringifies it, so
                # a bare date would hash differently than scraped rows do.
                event_date = datetime.strptime(override_date, "%Y-%m-%d")
            except ValueError:
                pass

        override_doors = request.form.get("override_doors", "").strip()
        if override_doors:
            doors_time = parse_time(override_doors) or time_type(19, 0)
        else:
            doors_time = parse_time(data.get("doors_open")) or time_type(19, 0)

        venue, _discarded = get_or_create_venue(
            name=venue_name,
            address=street,
            city=city,
            canton=resolve_canton(data.get("region") or "", city),
            plz=plz,
            coords=data.get("coords") or "",
        )
        new_event = create_event(
            name=name,
            date=event_date,
            doors=doors_time,
            genre=genre,
            acts=acts,
            description=description or None,
            source_url=source_url or None,
            flyer=data.get("flyer"),
            ticket_link=ticket_link,
            ticket_price=ticket_price,
            venue_id=venue.id,
            submitter_id=submitter.id,
            reject_duplicate=True,
        )
        if new_event is None:
            flash(_("This event already exists."))
            return redirect(url_for("submissions.event_queue"))
        db.session.flush()
        rec.approved = True
        rec.approved_at = datetime.now()
        rec.approved_event_id = new_event.id
        db.session.commit()
        bust_cache()
        flash(_("Event approved and added to calendar!"))
        return redirect(url_for("submissions.event_queue", **_queue_redirect_args()))
    now = datetime.now().date()
    return render_template(
        "event_queue.html",
        events=events,
        filter_date_from=(filter_date_from or now).isoformat(),
        filter_date_to=(filter_date_to or (now + relativedelta(months=2))).isoformat(),
        filter_source=filter_source or "",
        delete_form=DeleteScrapedEventForm(),
    )


@bp.route("/queue/<int:scraped_id>/delete", methods=["POST"])
@admin_required
def delete_scraped_event(scraped_id):
    form = DeleteScrapedEventForm()
    if not form.validate_on_submit():
        abort(400)
    scraped = ScrapedEvent.query.get_or_404(scraped_id)
    scraped.approved = True
    scraped.approved_at = datetime.now()
    db.session.commit()
    flash(_("Event removed from queue."))
    return redirect(url_for("submissions.event_queue", **_queue_redirect_args()))


@bp.route("/submit", methods=["GET", "POST"])
@login_required
def submit_event_link():
    form = EventForm()
    form.label_id.choices = all_label_choices()
    submitter = db.session.get(Submitter, session["user_id"])

    if form.validate_on_submit():
        flyer = save_flyer_file(
            form.flyer.data, current_app.config.get("UPLOAD_FOLDER", UPLOAD_FOLDER)
        )

        venue, created, error = resolve_venue_from_form(
            form, require_new_venue_details=True
        )
        if error == "missing_details":
            flash(_("Please provide all required venue details for a new venue."))
            return redirect(url_for("submissions.submit_event_link"))
        if error == "not_found":
            flash(_("Selected venue does not exist."))
            return redirect(url_for("submissions.submit_event_link"))
        if not created and form.venue_id.data in (None, "", "new"):
            flash(_("Venue already exists. Using existing venue."))

        create_event(
            name=form.name.data,
            date=form.date.data,
            end_date=form.end_date.data,
            is_festival=form.is_festival.data,
            doors=form.doors.data,
            genre=clean_genre_string(form.genre.data or []),
            acts=form.acts.data,
            flyer=flyer,
            ticket_link=form.ticket_link.data,
            ticket_price=form.ticket_price.data,
            venue_id=venue.id,
            submitter_id=submitter.id,
            label_id=int(form.label_id.data) if form.label_id.data else None,
        )
        db.session.commit()
        bust_cache()

        flash(_("Event submitted successfully!"))
        return redirect(url_for("public.calendar_view"))

    return render_template("submit_event.html", form=form)


@bp.route("/genres", methods=["GET", "POST"])
@login_required
def manage_genres():
    """The genre catalog page: any logged-in user can add a genre (it shows
    up in the event form's picker immediately) and delete genres they added
    themselves. Seed genres (added_by NULL) are deletable by admins only."""
    submitter = db.session.get(Submitter, session["user_id"])
    form = GenreForm()
    if form.validate_on_submit():
        name = " ".join(form.name.data.split())
        existing = find_genre(name)
        if existing:
            flash(_("'%(name)s' is already in the list.", name=existing.name))
        elif name:
            db.session.add(Genre(name=name, added_by_id=submitter.id))
            db.session.commit()
            bust_cache()
            flash(_("Genre '%(name)s' added.", name=name))
        return redirect(url_for("submissions.manage_genres"))
    genres = sorted(Genre.query.all(), key=lambda g: g.name.lower())
    return render_template(
        "genre_manage.html", form=form, genres=genres, submitter=submitter
    )


@bp.route("/genres/submit")
@login_required
def genre_submissions():
    """Old 'suggest a genre' URL — genres are user-managed now."""
    return redirect(url_for("submissions.manage_genres"))


@bp.route("/genres/<int:genre_id>/delete", methods=["POST"])
@login_required
def delete_genre(genre_id):
    submitter = db.session.get(Submitter, session["user_id"])
    genre = db.session.get(Genre, genre_id)
    if genre is None:
        abort(404)
    if not submitter.is_admin and genre.added_by_id != submitter.id:
        abort(403)
    name = genre.name
    db.session.delete(genre)
    db.session.commit()
    bust_cache()
    flash(_("Genre '%(name)s' removed.", name=name))
    return redirect(url_for("submissions.manage_genres"))
