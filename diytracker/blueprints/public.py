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

from diytracker.models import Event, Label, Venue, VenueAccessibility, db, utcnow
from diytracker.services.auth import safe_redirect_target
from diytracker.services.cache import cache
from diytracker.services.contact import build_contact_logger, send_contact_email
from diytracker.services.i18n import (
    DEFAULT_LOCALE,
    SUPPORTED_LOCALES,
    gettext as _,
    validate_lang,
)
from diytracker.services.archive import (
    adjacent_months,
    archive_directory,
    archive_month_events,
)
from diytracker.services.cantons import canton_directory
from diytracker.services.genres import genre_directory
from diytracker.services.scrape_detection import HONEYPOT_PATH
from diytracker.services.seo import (
    canonical_url,
    event_json_ld,
    parse_swiss_coords,
    slugify,
    venue_json_ld,
)
from diytracker.utils import CANTONS, parent_genres, resolve_canton

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


def _months_data(months):
    """[(year, month)] -> the months_data structure calendar.html iterates."""
    return [
        {
            "year": year,
            "month": month,
            "last_day_of_month": calendar.monthrange(year, month)[1],
        }
        for year, month in months
    ]


def _group_events_by_date(events):
    grouped_events = defaultdict(list)
    for event in events:
        grouped_events[event.date.date()].append(event)
    return grouped_events


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

    return render_template(
        "calendar.html",
        grouped_events=_group_events_by_date(events),
        months_data=_months_data(months),
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


# Placeholder until the drafts in templates/legal/ pass legal review; then
# switch to render_template("legal.html", doc=doc).
@bp.route("/<lang>/<any(impressum, agb, datenschutz):doc>")
def legal(lang, doc):
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
    # Event 397 was deleted by mistake; forward its (widely shared) URL to
    # the recreated event.
    if event_id == 397:
        return redirect(url_for("public.event_page", event_id=557), code=301)
    event = (
        Event.query.options(joinedload(Event.venue))
        .filter_by(id=event_id)
        .first_or_404()
    )
    # Past events stay live (they hold rankings for band/venue queries) but
    # get a visible notice plus pointers to what's coming up instead.
    now = datetime.now()
    is_past = (
        datetime.combine(event.end_date, event.doors) if event.end_date else event.date
    ) < now
    more_at_venue = []
    if is_past:
        more_at_venue = (
            Event.query.filter(Event.venue_id == event.venue_id, Event.date >= now)
            .order_by(Event.date.asc())
            .limit(5)
            .all()
        )
    canton_name = CANTONS.get(resolve_canton(event.venue.canton, event.venue.city))
    canton_slug = slugify(canton_name) if canton_name else None
    if canton_slug not in canton_directory():
        canton_slug = canton_name = None
    # Parent-genre links, limited to genres that have a live landing page.
    genre_links = [
        (slugify(name), name)
        for name in parent_genres(event.genre)
        if slugify(name) in genre_directory()
    ]
    return render_template(
        "event_page.html",
        event=event,
        event_ld=event_json_ld(event),
        is_past=is_past,
        more_at_venue=more_at_venue,
        canton_slug=canton_slug,
        canton_name=canton_name,
        genre_links=genre_links,
    )


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
        parsed = parse_swiss_coords(venue.coords)
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
    coords = parse_swiss_coords(venue.coords)
    return render_template(
        "venue_page.html",
        venue=venue,
        events=events,
        coords=coords,
        venue_ld=venue_json_ld(venue, coords),
    )


@bp.route("/<canton_slug:canton_slug>/")
def canton_page(canton_slug):
    # The canton_slug converter excludes reserved segments at routing time
    # (so /logout etc. keep their behavior), and static rules take
    # precedence anyway; unknown-but-valid slugs 404 here.
    info = canton_directory().get(canton_slug)
    if info is None:
        abort(404)
    events = (
        Event.query.options(joinedload(Event.venue))
        .filter(Event.venue_id.in_(info["venue_ids"]), Event.date >= datetime.now())
        .order_by(Event.date.asc())
        .all()
    )
    venues = (
        Venue.query.filter(Venue.id.in_(info["venue_ids"]))
        .order_by(Venue.name.asc())
        .all()
    )
    return render_template(
        "canton.html", canton=info["name"], events=events, venues=venues
    )


@bp.route("/genre/<genre_slug>/")
def genre_page(genre_slug):
    # Like canton pages, only genres with an upcoming event resolve; the
    # directory maps slugs to canonical parent genre names ("Other" excluded).
    info = genre_directory().get(genre_slug)
    if info is None:
        abort(404)
    events = (
        Event.query.options(joinedload(Event.venue))
        .filter(
            Event.parent_genres.like(f"%,{info['name']},%"),
            Event.date >= datetime.now(),
        )
        .order_by(Event.date.asc())
        .all()
    )
    venues = (
        Venue.query.filter(Venue.id.in_(info["venue_ids"]))
        .order_by(Venue.name.asc())
        .all()
    )
    live_cantons = canton_directory()
    cantons = sorted(
        (slug, live_cantons[slug]["name"])
        for slug in info["canton_slugs"]
        if slug in live_cantons
    )
    return render_template(
        "genre.html",
        genre=info["name"],
        events=events,
        venues=venues,
        cantons=cantons,
    )


def _path_locale_cache_key(*_args, **_kwargs):
    # flask-caching passes the view args through; the request path already
    # encodes them, so the key only needs path + locale. Shared by the
    # archive and label pages.
    return f"page:{request.path}:{getattr(g, 'locale', DEFAULT_LOCALE)}"


@bp.route("/label/<label_slug>/")
@cache.cached(make_cache_key=_path_locale_cache_key, unless=_skip_calendar_cache)
def label_page(label_slug):
    # Label pages stay live even with no upcoming events (unlike genre
    # pages): promoters share the URL before their first show is posted.
    label = Label.query.filter_by(slug=label_slug).first_or_404()
    events = (
        Event.query.options(joinedload(Event.venue))
        .filter(Event.label_id == label.id, Event.date >= datetime.now())
        .order_by(Event.date.asc())
        .all()
    )
    # All upcoming shows, so the month span follows the events rather than
    # the calendar's fixed 3-month window.
    months = []
    if events:
        cursor = events[0].date.date().replace(day=1)
        last = events[-1].date.date().replace(day=1)
        while cursor <= last:
            months.append((cursor.year, cursor.month))
            cursor += relativedelta(months=1)
    return render_template(
        "label_page.html",
        label=label,
        grouped_events=_group_events_by_date(events),
        months_data=_months_data(months),
        datetime=datetime,
    )


@bp.route("/archive/")
@cache.cached(make_cache_key=_path_locale_cache_key, unless=_skip_calendar_cache)
def archive_index():
    # Not to be confused with /events/archive/, the scrape-detection
    # honeypot below — this is the real, visible archive.
    return render_template(
        "archive_index.html", directory=archive_directory(), datetime=datetime
    )


@bp.route("/archive/<int:year>/<int:month>/")
@cache.cached(make_cache_key=_path_locale_cache_key, unless=_skip_calendar_cache)
def archive_month(year, month):
    months = dict(archive_directory().get(year, []))
    if month not in months:
        abort(404)
    prev_month, next_month = adjacent_months(year, month)
    return render_template(
        "archive_month.html",
        year=year,
        month=month,
        month_date=datetime(year, month, 1),
        grouped_events=archive_month_events(year, month),
        event_count=months[month],
        prev_month=prev_month,
        next_month=next_month,
        datetime=datetime,
    )


@bp.route("/sitemap.xml")
@cache.cached()
def sitemap():
    # (loc, changefreq, lastmod ISO date or None). Legal pages are excluded
    # while their routes still return "WIP" placeholders.
    pages = [
        (canonical_url(url_for("public.calendar_view")), "daily", None),
        (canonical_url(url_for("public.about")), "monthly", None),
        (canonical_url(url_for("public.venue_map")), "weekly", None),
    ]

    for slug in sorted(canton_directory()):
        pages.append(
            (
                canonical_url(url_for("public.canton_page", canton_slug=slug)),
                "daily",
                None,
            )
        )

    for slug in sorted(genre_directory()):
        pages.append(
            (
                canonical_url(url_for("public.genre_page", genre_slug=slug)),
                "daily",
                None,
            )
        )

    for label in Label.query.order_by(Label.slug.asc()).all():
        pages.append(
            (
                canonical_url(url_for("public.label_page", label_slug=label.slug)),
                "daily",
                label.updated_at.date().isoformat() if label.updated_at else None,
            )
        )

    # Archive pages follow the same two-year sitemap horizon as past events
    # below; older months stay reachable through the archive index.
    horizon = datetime.now() - relativedelta(years=2)
    pages.append((canonical_url(url_for("public.archive_index")), "monthly", None))
    for year, month_counts in archive_directory().items():
        for month, _count in month_counts:
            if datetime(year, month, 1) + relativedelta(months=1) < horizon:
                continue
            pages.append(
                (
                    canonical_url(
                        url_for("public.archive_month", year=year, month=month)
                    ),
                    "monthly",
                    None,
                )
            )

    # Past events stay listed for two years — they keep ranking for band and
    # venue queries — then age out of the sitemap (the pages themselves stay).
    events = Event.query.filter(Event.date >= horizon).order_by(Event.date.asc()).all()
    for event in events:
        pages.append(
            (
                canonical_url(url_for("public.event_page", event_id=event.id)),
                "weekly",
                event.updated_at.date().isoformat() if event.updated_at else None,
            )
        )

    accessibility = {
        row.venue_id: row.updated_at
        for row in db.session.query(
            VenueAccessibility.venue_id, VenueAccessibility.updated_at
        )
    }
    venues = Venue.query.order_by(Venue.id.asc()).all()
    for venue in venues:
        lastmod_candidates = [venue.updated_at, accessibility.get(venue.id)]
        lastmod = max((ts for ts in lastmod_candidates if ts), default=None)
        pages.append(
            (
                canonical_url(url_for("public.venue_page", venue_id=venue.id)),
                "weekly",
                lastmod.date().isoformat() if lastmod else None,
            )
        )
        if venue.id in accessibility:
            pages.append(
                (
                    canonical_url(
                        url_for("public.venue_accessibility", venue_id=venue.id)
                    ),
                    "monthly",
                    accessibility[venue.id].date().isoformat()
                    if accessibility[venue.id]
                    else None,
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
            # Awario's brand-monitoring crawler was the single heaviest
            # client in the July 2026 traffic audit (~17% of all requests)
            # with zero value to us. It honours robots.txt per
            # https://awario.com/bots.html — all its agent names are banned.
            "User-agent: AwarioBot",
            "User-agent: AwarioRssBot",
            "User-agent: AwarioSmartBot",
            "Disallow: /",
            "",
            "User-agent: *",
            f"Disallow: {HONEYPOT_PATH}",
            "Disallow: /admin",
            "Disallow: /login",
            "",
            f"Sitemap: {canonical_url(url_for('public.sitemap'))}",
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
