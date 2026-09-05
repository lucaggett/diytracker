import contextlib
from datetime import datetime
from datetime import time as time_type

from dateutil.relativedelta import relativedelta
from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)

from diytracker.forms import DeleteScrapedEventForm, EventForm, GenreForm
from diytracker.models import Event, Genre, ScrapedEvent, Venue, db
from diytracker.services import queue_review
from diytracker.services.audit import record
from diytracker.services.auth import admin_required, current_user, login_required
from diytracker.services.cache import bust_cache
from diytracker.services.errors import AdminError
from diytracker.services.events import (
    clean_genre_string,
    create_event,
    resolve_venue_from_form,
)
from diytracker.services.genre_catalog import find_genre
from diytracker.services.i18n import gettext as _
from diytracker.services.ingest import parse_time
from diytracker.services.ingest_dedup import find_duplicate_matches, is_strong_match
from diytracker.services.labels import owned_label_choices
from diytracker.services.search import like_patterns, normalise_query
from diytracker.services.uploads import UPLOAD_FOLDER, save_flyer_file
from diytracker.services.venue import (
    PLACEHOLDER_VENUE_NAME,
    get_or_create_venue,
    resolve_existing_venue,
)
from diytracker.utils import is_safe_link, resolve_canton

bp = Blueprint("submissions", __name__)

_ALLOWED_QUEUE_PARAMS = {"date_from", "date_to", "source", "q", "page"}
QUEUE_PAGE_SIZE = 50


def _queue_redirect_args():
    """Preserve the queue's filter params across a POST redirect."""
    return {k: v for k, v in request.args.items() if k in _ALLOWED_QUEUE_PARAMS}


def _scraped_event_to_dict(rec):
    data = {
        "source": rec.source,
        "url": rec.url,
        "title": rec.title,
        "performers": rec.performers or rec.title,
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


def parse_scraped_events(date_from=None, date_to=None, source=None, q=None, page=1):
    """Pending queue rows within the filters, as (dicts, pagination)."""
    now = datetime.now().date()
    if date_from is None:
        date_from = now
    if date_to is None:
        date_to = now + relativedelta(months=2)
    query = ScrapedEvent.query.filter(
        ScrapedEvent.status == ScrapedEvent.STATUS_PENDING,
        # Flagged possible-duplicates are kept out of the main web queue;
        # they're triaged on /queue/duplicates or in the admin TUI.
        ScrapedEvent.needs_review.is_(False),
        ScrapedEvent.start_date >= date_from,
        ScrapedEvent.start_date <= date_to,
    )
    if source:
        query = query.filter(ScrapedEvent.source == source)
    raw_q = normalise_query(q or "")
    if raw_q:
        for pattern in like_patterns(raw_q):
            query = query.filter(
                db.or_(
                    ScrapedEvent.title.ilike(pattern, escape="\\"),
                    ScrapedEvent.performers.ilike(pattern, escape="\\"),
                    ScrapedEvent.venue_name.ilike(pattern, escape="\\"),
                    ScrapedEvent.city.ilike(pattern, escape="\\"),
                    ScrapedEvent.organizer.ilike(pattern, escape="\\"),
                )
            )
    pagination = query.order_by(ScrapedEvent.start_date.asc()).paginate(
        page=page, per_page=QUEUE_PAGE_SIZE, error_out=False
    )
    return [_scraped_event_to_dict(rec) for rec in pagination.items], pagination


def _approval_confirmation(
    rec, data, overrides, venue_candidates=(), duplicates=(), forced_venue=None
):
    """Stop an approval and ask, instead of writing a near-duplicate.

    Renders the staged entry beside whatever it ran into, with every override
    re-emitted as a hidden field so the answer is the same POST plus a decision:
    ``confirm_venue_id`` (an existing id, or "new") and/or ``confirm_duplicate``.
    Nothing has been written when this returns — both callers run before the
    first ``db.session.add``.
    """
    counts = dict(
        db.session.query(Event.venue_id, db.func.count(Event.id))
        .filter(Event.venue_id.in_([v.id for v in venue_candidates] or [0]))
        .group_by(Event.venue_id)
    )
    return render_template(
        "queue_approve_confirm.html",
        rec=data,
        scraped_id=rec.id,
        overrides=overrides,
        venue_candidates=[
            {
                "id": v.id,
                "name": v.name.strip(),
                "city": (v.city or "").strip(),
                "plz": (v.plz or "").strip(),
                "address": (v.address or "").strip(),
                "events": counts.get(v.id, 0),
            }
            for v in venue_candidates
        ],
        duplicates=list(duplicates),
        forced_venue=forced_venue,
        queue_args=_queue_redirect_args(),
    )


# Admin-only: approving a queue entry publishes it to the live calendar, and
# deleting one is permanent. An invite grants /submit, not moderation.
@bp.route("/queue", methods=["GET", "POST"])
@admin_required
def event_queue():
    submitter = current_user()

    def _parse_date(key):
        raw = request.args.get(key, "").strip()
        try:
            return datetime.strptime(raw, "%Y-%m-%d").date() if raw else None
        except ValueError:
            return None

    filter_date_from = _parse_date("date_from")
    filter_date_to = _parse_date("date_to")
    filter_source = request.args.get("source", "").strip() or None
    filter_q = request.args.get("q", "").strip() or None
    page = request.args.get("page", 1, type=int)
    events, pagination = parse_scraped_events(
        date_from=filter_date_from,
        date_to=filter_date_to,
        source=filter_source,
        q=filter_q,
        page=page,
    )
    if request.method == "POST":
        try:
            scraped_id = int(request.form.get("scraped_id"))
        except (TypeError, ValueError):
            scraped_id = None
        rec = db.session.get(ScrapedEvent, scraped_id) if scraped_id else None
        if rec is None or rec.status != ScrapedEvent.STATUS_PENDING:
            flash(_("Invalid event selection."))
            return redirect(url_for("submissions.event_queue"))
        data = _scraped_event_to_dict(rec)

        def _ov(key, fallback):
            # Both sides are stripped: a stored venue name with a trailing
            # space ("Sedel ") is a different venue to an exact lookup, and
            # that is how the second "Sedel" got created.
            val = request.form.get(key, "").strip()
            return val or (fallback or "").strip()

        name = _ov(
            "override_name", data.get("title") or data.get("performers") or "Concert"
        )
        venue_name = _ov(
            "override_venue_name", data.get("venue_name") or PLACEHOLDER_VENUE_NAME
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
            # Kept a datetime (not ingest.parse_date's date): Event.date
            # is a DateTime column and the dedup hash stringifies it, so
            # a bare date would hash differently than scraped rows do.
            with contextlib.suppress(ValueError):
                event_date = datetime.strptime(override_date, "%Y-%m-%d")

        override_doors = request.form.get("override_doors", "").strip()
        if override_doors:
            doors_time = parse_time(override_doors) or time_type(19, 0)
        else:
            doors_time = parse_time(data.get("doors_open")) or time_type(19, 0)

        # Two gates before anything is written. Approving used to look the
        # venue up by the exact (name, city, plz) tuple and trust event_hash to
        # catch a repeat, which let a burst of approvals mint a venue apiece and
        # publish shows that were already on the calendar.
        confirm_venue = request.form.get("confirm_venue_id", "").strip()
        forced_venue = None
        venue, candidates = None, []
        if confirm_venue == "new":
            forced_venue = "new"
        elif confirm_venue.isdigit():
            venue = db.session.get(Venue, int(confirm_venue))
            if venue is None:
                flash(_("That venue no longer exists."))
                return redirect(
                    url_for("submissions.event_queue", **_queue_redirect_args())
                )
            forced_venue = str(venue.id)
        else:
            venue, candidates = resolve_existing_venue(venue_name, city, plz, street)

        overrides = {
            "override_name": name,
            "override_venue_name": venue_name,
            "override_city": city,
            "override_postal_code": plz,
            "override_street_address": street,
            "override_genre": raw_genre,
            "override_acts": acts,
            "override_ticket_price": ticket_price,
            "override_ticket_url": ticket_link,
            "override_description": description,
            "override_source_url": source_url,
            "override_date": event_date.strftime("%Y-%m-%d") if event_date else "",
            "override_doors": doors_time.strftime("%H:%M"),
        }

        if venue is None and candidates:
            return _approval_confirmation(
                rec, data, overrides, venue_candidates=candidates
            )

        # Re-run the matcher against the final values rather than trusting the
        # review_reason written at ingest, which is a snapshot and says nothing
        # about what has been published since.
        confirmed_duplicate = request.form.get("confirm_duplicate") == "1"
        strong = [
            m
            for m in find_duplicate_matches(
                event_date.date() if event_date else None,
                city,
                venue.name if venue else venue_name,
                name,
                exclude_scraped_id=rec.id,
            )
            if is_strong_match(m)
        ]
        if strong and not confirmed_duplicate:
            return _approval_confirmation(
                rec, data, overrides, duplicates=strong, forced_venue=forced_venue
            )

        if venue is None:
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
        rec.status = ScrapedEvent.STATUS_PUBLISHED
        rec.approved_at = datetime.now()
        rec.approved_event_id = new_event.id
        # The Event carries the approving admin as submitter_id; the scraped
        # attribution survives only here.
        record(
            "queue.approve",
            "scraped_event",
            rec.id,
            actor=submitter,
            detail=(
                f"event_id={new_event.id} name={name!r} venue_id={venue.id} "
                f"source={data.get('source')!r} url={data.get('url')!r} "
                f"organizer={data.get('organizer')!r} "
                f"submitter={data.get('submitter')!r}"
                + (f" forced_venue={forced_venue}" if forced_venue else "")
                + (
                    f" override_duplicate={[m['kind'] + '#' + str(m['id']) for m in strong]}"
                    if strong
                    else ""
                )
            ),
        )
        db.session.commit()
        bust_cache()
        flash(_("Event approved and added to calendar!"))
        return redirect(url_for("submissions.event_queue", **_queue_redirect_args()))
    now = datetime.now().date()
    dup_count = ScrapedEvent.query.filter(
        ScrapedEvent.status == ScrapedEvent.STATUS_PENDING,
        ScrapedEvent.needs_review.is_(True),
    ).count()
    return render_template(
        "event_queue.html",
        events=events,
        pagination=pagination,
        filter_date_from=(filter_date_from or now).isoformat(),
        filter_date_to=(filter_date_to or (now + relativedelta(months=2))).isoformat(),
        filter_source=filter_source or "",
        filter_q=filter_q or "",
        dup_count=dup_count,
        delete_form=DeleteScrapedEventForm(),
    )


def _reject(scraped, actor, reason):
    """Mark one pending row rejected and stage the audit entry."""
    scraped.status = ScrapedEvent.STATUS_REJECTED
    scraped.approved_at = datetime.now()
    scraped.reject_reason = reason or None
    record(
        "queue.reject",
        "scraped_event",
        scraped.id,
        actor=actor,
        detail=(
            f"title={scraped.title!r} source={scraped.source!r} "
            f"url={scraped.url!r} reason={reason!r}"
        ),
    )


@bp.route("/queue/<int:scraped_id>/delete", methods=["POST"])
@admin_required
def delete_scraped_event(scraped_id):
    form = DeleteScrapedEventForm()
    if not form.validate_on_submit():
        abort(400)
    scraped = ScrapedEvent.query.get_or_404(scraped_id)
    reason = request.form.get("reject_reason", "").strip()[:300]
    _reject(scraped, current_user(), reason)
    db.session.commit()
    flash(_("Event removed from queue."))
    return redirect(url_for("submissions.event_queue", **_queue_redirect_args()))


@bp.route("/queue/bulk-reject", methods=["POST"])
@admin_required
def bulk_reject_scraped_events():
    form = DeleteScrapedEventForm()
    if not form.validate_on_submit():
        abort(400)
    ids = [int(x) for x in request.form.getlist("scraped_ids") if x.isdigit()]
    reason = request.form.get("reject_reason", "").strip()[:300]
    actor = current_user()
    rows = ScrapedEvent.query.filter(
        ScrapedEvent.id.in_(ids),
        ScrapedEvent.status == ScrapedEvent.STATUS_PENDING,
    ).all()
    for scraped in rows:
        _reject(scraped, actor, reason)
    db.session.commit()
    flash(_("%(num)d events removed from queue.", num=len(rows)))
    return redirect(url_for("submissions.event_queue", **_queue_redirect_args()))


@bp.route("/queue/duplicates")
@admin_required
def queue_duplicates():
    rows, pagination = queue_review.list_flagged(
        page=request.args.get("page", 1, type=int), per_page=QUEUE_PAGE_SIZE
    )
    return render_template(
        "queue_duplicates.html",
        rows=rows,
        pagination=pagination,
        delete_form=DeleteScrapedEventForm(),
    )


@bp.route("/queue/duplicates/bulk/<action>", methods=["POST"])
@admin_required
def bulk_resolve_queue_duplicates(action):
    """Discard or unflag every checked row in one round trip.

    Clearing a konzibot re-push batch row by row is one confirm and one full
    page reload each; this mirrors bulk_reject_scraped_events for the flagged
    queue. Ids that went stale while the page was open are skipped, not fatal.
    """
    form = DeleteScrapedEventForm()
    if not form.validate_on_submit() or action not in ("discard", "unflag"):
        abort(400)
    ids = [int(x) for x in request.form.getlist("scraped_ids") if x.isdigit()]
    actor = current_user()
    if action == "discard":
        count = queue_review.discard_many(ids, actor=actor)
        flash(_("%(num)d entries discarded.", num=count))
    else:
        count = queue_review.unflag_many(ids, actor=actor)
        flash(_("%(num)d entries unflagged — they are back in the queue.", num=count))
    return redirect(url_for("submissions.queue_duplicates"))


@bp.route("/queue/duplicates/<int:scraped_id>/<action>", methods=["POST"])
@admin_required
def resolve_queue_duplicate(scraped_id, action):
    form = DeleteScrapedEventForm()
    if not form.validate_on_submit() or action not in ("discard", "unflag"):
        abort(400)
    actor = current_user()
    try:
        if action == "discard":
            row = queue_review.discard(scraped_id, actor=actor)
            flash(_("Discarded “%(title)s”.", title=row.title))
        else:
            row = queue_review.unflag(scraped_id, actor=actor)
            flash(_("Unflagged “%(title)s” — it's back in the queue.", title=row.title))
    except AdminError as exc:
        flash(str(exc))
    return redirect(url_for("submissions.queue_duplicates"))


@bp.route("/submit", methods=["GET", "POST"])
@login_required
def submit_event_link():
    form = EventForm()
    submitter = current_user()
    form.label_id.choices = owned_label_choices(submitter)

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
    themselves. Seed genres (added_by NULL) are deletable by admins only.
    """
    submitter = current_user()
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
    submitter = current_user()
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
