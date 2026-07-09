import calendar
from collections import defaultdict
from datetime import datetime

from dateutil.relativedelta import relativedelta
from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from diytracker.forms import AccessibilityForm, CollaboratorRequestForm
from sqlalchemy.orm import joinedload

from diytracker.models import Event, Venue, VenueAccessibility, db, utcnow
from diytracker.services.auth import safe_redirect_target
from diytracker.services.cache import cache
from diytracker.services.contact import build_contact_logger, send_contact_email
from diytracker.services.i18n import (
    DEFAULT_LOCALE,
    SUPPORTED_LOCALES,
    gettext as _,
    validate_lang,
)
from diytracker.services.scrape_detection import HONEYPOT_PATH

bp = Blueprint("public", __name__)

_contact_logger = None


def _get_contact_logger():
    global _contact_logger
    if _contact_logger is None:
        _contact_logger = build_contact_logger()
    return _contact_logger


def _calendar_cache_key():
    return f"calendar_view:{getattr(g, 'locale', DEFAULT_LOCALE)}"


def _skip_calendar_cache():
    # Logged-in users and requests with pending flash messages must not be
    # cached: the cache is keyed only on locale, so their rendered flashes
    # would be served to every visitor until the entry expires.
    return "user_id" in session or bool(session.get("_flashes"))


@bp.after_request
def _public_cache_headers(response):
    if request.endpoint != "public.calendar_view" or response.status_code != 200:
        return response
    if "user_id" in session:
        return response
    # Content varies by locale (session cookie + Accept-Language), so shared
    # caches need more than Accept-Encoding to key on.
    response.vary.update(("Cookie", "Accept-Language", "Accept-Encoding"))
    response.headers["Cache-Control"] = "public, max-age=300"
    response.add_etag()
    return response.make_conditional(request)


@bp.route("/")
@cache.cached(make_cache_key=_calendar_cache_key, unless=_skip_calendar_cache)
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

    events = (
        Event.query.options(joinedload(Event.venue))
        .filter(Event.date >= start_date, Event.date <= end_date)
        .order_by(Event.date.asc())
        .all()
    )

    grouped_events = defaultdict(list)
    for event in events:
        event_date = event.date.date()
        grouped_events[event_date].append(event)

    months_data = []
    for year, month in months:
        last_day_of_month = calendar.monthrange(year, month)[1]
        months_data.append(
            {
                "year": year,
                "month": month,
                "last_day_of_month": last_day_of_month,
            }
        )

    return render_template(
        "calendar.html",
        grouped_events=grouped_events,
        months_data=months_data,
        datetime=datetime,
    )


@bp.route("/about", methods=["GET", "POST"])
def about():
    form = CollaboratorRequestForm()
    if form.validate_on_submit():
        name = form.name.data.strip()
        sender_email = form.email.data.strip()
        message = form.message.data.strip()
        honeypot_filled = bool((form.website.data or "").strip())
        single_line_message = message.replace("\n", " \\n ")
        logger = _get_contact_logger()
        if honeypot_filled:
            logger.info(
                "honeypot name=%r email=%r message=%r",
                name,
                sender_email,
                single_line_message,
            )
        else:
            try:
                send_contact_email(name, sender_email, message)
            except Exception as exc:
                logger.exception(
                    "send_failed name=%r email=%r message=%r error=%s",
                    name,
                    sender_email,
                    single_line_message,
                    exc,
                )
                current_app.logger.exception("Failed to send collaborator email")
                flash(
                    _(
                        "Your message could not be sent — please write to us directly at kontakt@diytracker.ch."
                    )
                )
                return redirect(url_for("public.about"))
            logger.info(
                "sent name=%r email=%r message=%r",
                name,
                sender_email,
                single_line_message,
            )
        flash(_("Thanks! We will get back to you as soon as possible."))
        return redirect(url_for("public.about"))
    return render_template("about.html", form=form)


@bp.route("/<lang>/impressum")
def impressum(lang):
    validate_lang(lang)
    return "WIP"


@bp.route("/<lang>/agb")
def agb(lang):
    validate_lang(lang)
    return "WIP"


@bp.route("/<lang>/datenschutz")
def datenschutz(lang):
    validate_lang(lang)
    return "WIP"


@bp.route("/accessibility/<token>", methods=["GET", "POST"])
def accessibility_form(token):
    venue = Venue.query.filter_by(accessibility_token=token).first_or_404()
    info = venue.accessibility
    if info is None:
        info = VenueAccessibility(venue_id=venue.id)
    form = AccessibilityForm(obj=info)
    if form.validate_on_submit():
        form.populate_obj(info)
        info.venue_id = venue.id
        info.updated_at = utcnow()
        db.session.add(info)
        db.session.commit()
        flash(_("Accessibility info saved — thank you!"))
        return redirect(url_for("public.accessibility_form", token=token))
    return render_template("accessibility_form.html", form=form, venue=venue, info=info)


@bp.route("/venues/<int:venue_id>/accessibility")
def venue_accessibility(venue_id):
    venue = Venue.query.get_or_404(venue_id)
    return render_template(
        "venue_accessibility.html", venue=venue, info=venue.accessibility
    )


@bp.route("/events/<int:event_id>/")
def event_page(event_id):
    event = (
        Event.query.options(joinedload(Event.venue))
        .filter_by(id=event_id)
        .first_or_404()
    )
    return render_template("event_page.html", event=event)


def _parse_swiss_coords(coords):
    """Parse a 'lat,lon' string; None if malformed or outside the Swiss
    bounding box. The map is a cutout of Switzerland with panning locked to
    it, so venues outside the box would be unreachable anyway."""
    try:
        lat, lon = (float(part) for part in (coords or "").split(","))
    except ValueError:
        return None
    if not (45.6 <= lat <= 48.0 and 5.7 <= lon <= 10.7):
        return None
    return lat, lon


@bp.route("/map")
def venue_map():
    # Same horizon as the calendar: now through three months out.
    now = datetime.now()
    upcoming_counts = dict(
        db.session.query(Event.venue_id, db.func.count(Event.id))
        .filter(Event.date >= now, Event.date <= now + relativedelta(months=3))
        .group_by(Event.venue_id)
        .all()
    )

    venues = (
        Venue.query.filter(
            Venue.coords.isnot(None),
            Venue.coords != "",
        )
        .order_by(Venue.name.asc())
        .all()
    )

    markers = []
    for venue in venues:
        parsed = _parse_swiss_coords(venue.coords)
        if parsed is None:
            continue
        lat, lon = parsed
        markers.append(
            {
                "id": venue.id,
                "name": venue.name,
                "lat": lat,
                "lon": lon,
                "address": venue.address or "",
                "city": venue.city,
                "plz": venue.plz or "",
                "upcoming": upcoming_counts.get(venue.id, 0),
            }
        )

    return render_template("map.html", markers=markers)


@bp.route("/venues/<int:venue_id>/")
def venue_page(venue_id):
    venue = Venue.query.get_or_404(venue_id)
    events = (
        Event.query.filter(Event.venue_id == venue.id, Event.date >= datetime.now())
        .order_by(Event.date.asc())
        .all()
    )
    coords = _parse_swiss_coords(venue.coords)
    return render_template("venue_page.html", venue=venue, events=events, coords=coords)


@bp.route("/sitemap.xml")
@cache.cached()
def sitemap():
    pages = [
        (url_for("public.calendar_view", _external=True), "daily"),
        (url_for("public.about", _external=True), "monthly"),
        (url_for("public.venue_map", _external=True), "weekly"),
    ]
    for lang in SUPPORTED_LOCALES:
        pages.append((url_for("public.impressum", lang=lang, _external=True), "yearly"))
        pages.append((url_for("public.agb", lang=lang, _external=True), "yearly"))
        pages.append(
            (url_for("public.datenschutz", lang=lang, _external=True), "yearly")
        )

    events = Event.query.order_by(Event.date.asc()).all()
    for event in events:
        pages.append(
            (url_for("public.event_page", event_id=event.id, _external=True), "weekly")
        )

    accessibility_ids = {
        venue_id for (venue_id,) in db.session.query(VenueAccessibility.venue_id)
    }
    venues = Venue.query.order_by(Venue.id.asc()).all()
    for venue in venues:
        pages.append(
            (url_for("public.venue_page", venue_id=venue.id, _external=True), "weekly")
        )
        if venue.id in accessibility_ids:
            pages.append(
                (
                    url_for(
                        "public.venue_accessibility", venue_id=venue.id, _external=True
                    ),
                    "monthly",
                )
            )

    xml = render_template("sitemap.xml", pages=pages)
    return Response(xml, mimetype="application/xml")


@bp.route("/robots.txt")
def robots():
    # The honeypot disallow doubles as bait: polite crawlers skip it,
    # scrapers that parse robots.txt for "interesting" paths walk into it.
    body = "\n".join(
        [
            "User-agent: *",
            f"Disallow: {HONEYPOT_PATH}",
            "Disallow: /admin",
            "Disallow: /login",
            "",
            f"Sitemap: {url_for('public.sitemap', _external=True)}",
            "",
        ]
    )
    return Response(body, mimetype="text/plain")


@bp.route(HONEYPOT_PATH)
def events_archive():
    # Scrape-detection honeypot (services/scrape_detection.py flags the hit
    # in its before_request hook). Render something plausible so scrapers
    # don't realise they've been spotted.
    return render_template("errors/404.html"), 200


@bp.route("/set-language/<lang>")
def set_language(lang):
    if lang not in SUPPORTED_LOCALES:
        abort(404)
    session["lang"] = lang
    next_url = safe_redirect_target(request.args.get("next", ""))
    if next_url:
        return redirect(next_url)
    return redirect(url_for("public.calendar_view"))


@bp.route("/kyuubi")
def kyuubi():
    return render_template("kyuubi.html")
