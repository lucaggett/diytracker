import calendar
from collections import defaultdict
from datetime import datetime, timedelta

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
from sqlalchemy import or_
from sqlalchemy.orm import joinedload

from diytracker.models import Event, Label, Venue, VenueAccessibility, db, utcnow
from diytracker.services.auth import safe_redirect_target
from diytracker.services.cache import cache
from diytracker.services.contact import build_contact_logger, send_contact_email
from diytracker.services.i18n import (
    DEFAULT_LOCALE,
    SUPPORTED_LOCALES,
    canton_in,
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
from diytracker.services.ics import build_calendar
from diytracker.services.limits import limiter
from diytracker.services.page_texts import get_page_text
from diytracker.services.scrape_detection import HONEYPOT_PATH
from diytracker.services.search import (
    normalise_query,
    search_events,
    search_venues,
)
from diytracker.services.seo import (
    canonical_url,
    collection_json_ld,
    event_json_ld,
    hreflang_entries,
    localized_paths,
    parse_swiss_coords,
    slugify,
    venue_json_ld,
)
from diytracker.utils import CANTONS, parent_genres, resolve_canton

bp = Blueprint("public", __name__)

# Non-default locales get URL-prefixed twins of the public pages (/fr/about,
# /it/zuerich/, ...) registered under the SAME endpoint; the default locale
# keeps the bare URL (canonical + hreflang x-default), so nothing Google has
# already indexed ever redirects. `de` is deliberately absent: /de/... 404s.
_LANG_PREFIX = "/<any(fr, it, en):lang_prefix>"


def localized_route(rule, **options):
    """@bp.route plus a locale-prefixed twin under the same endpoint, so
    url_for picks the right rule from the presence of `lang_prefix`."""

    def decorator(f):
        endpoint = options.pop("endpoint", f.__name__)
        bp.add_url_rule(rule, endpoint, f, **options)
        bp.add_url_rule(_LANG_PREFIX + rule, endpoint, f, **options)
        return f

    return decorator


@bp.url_value_preprocessor
def _pull_lang_prefix(endpoint, values):
    if not values:
        return
    g.url_locale = values.pop("lang_prefix", None)
    # Legal pages carry their locale in <lang> instead; mirror it (but leave
    # it in values — the view still takes it) so Babel translates the page
    # chrome in the URL's language too.
    if endpoint == "public.legal":
        g.url_locale = values.get("lang")


@bp.url_defaults
def _inject_lang_prefix(endpoint, values):
    if "lang_prefix" in values:
        # Explicit None means "give me the default-locale URL" (hreflang and
        # sitemap builders); pop it so it can't leak as a query arg.
        if values["lang_prefix"] is None:
            values.pop("lang_prefix")
        return
    locale = g.get("locale", DEFAULT_LOCALE)
    if locale != DEFAULT_LOCALE and current_app.url_map.is_endpoint_expecting(
        endpoint, "lang_prefix"
    ):
        values["lang_prefix"] = locale


_contact_logger = None


def _get_contact_logger():
    global _contact_logger
    if _contact_logger is None:
        _contact_logger = build_contact_logger()
    return _contact_logger


def _calendar_cache_key():
    # Path AND locale: / and /fr/ hit the same endpoint but must not share a
    # cache entry (their canonical/hreflang self-URLs differ), while the bare
    # / still varies by session locale.
    return f"calendar_view:{request.path}:{getattr(g, 'locale', DEFAULT_LOCALE)}"


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


def _group_events_by_date(events):
    # Festivals (events with an end_date after their start) appear under every
    # day they run, not just the opening one.
    grouped_events = defaultdict(list)
    for event in events:
        start = event.date.date()
        last = event.end_date if event.end_date and event.end_date > start else start
        day = start
        while day <= last:
            grouped_events[day].append(event)
            day += timedelta(days=1)
    return grouped_events


def _upcoming_filter(now):
    # An event is "upcoming" until its last day has passed — a festival that
    # started yesterday but runs through the weekend must stay listed.
    return or_(Event.date >= now, Event.end_date >= now.date())


@bp.app_context_processor
def _inject_today_local():
    # _partials/event_cards.html needs today's date to drop past days and to
    # stamp the current one. Event.date is a wall-clock Swiss time, so this is
    # the local date, matching the `_upcoming_filter(datetime.now())` filters —
    # injected here so the partial can never be included without it.
    return {"today_local": datetime.now().date()}


@localized_route("/")
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
        .filter(
            Event.date <= end_date,
            or_(Event.date >= start_date, Event.end_date >= start_date.date()),
        )
        .order_by(Event.date.asc())
        .all()
    )

    return render_template(
        "calendar.html",
        grouped_events=_group_events_by_date(events),
    )


@localized_route("/about", methods=["GET", "POST"])
# Each POST sends a real email through our SMTP account. The honeypot stops
# dumb bots; this stops anyone who fills the form correctly from flooding the
# inbox or getting the sending account throttled.
@limiter.limit("5 per hour", methods=["POST"])
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


# AGB is still a placeholder until its draft passes review; impressum and
# datenschutz are live minimal versions (see templates/legal/).
@bp.route("/<lang>/<any(impressum, agb, datenschutz):doc>")
def legal(lang, doc):
    validate_lang(lang)
    if doc == "agb":
        return "WIP"
    return render_template("legal.html", doc=doc)


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


@localized_route("/events/<int:event_id>/")
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


@localized_route("/map")
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


@localized_route("/venues/<int:venue_id>/")
def venue_page(venue_id):
    venue = Venue.query.get_or_404(venue_id)
    events = (
        Event.query.filter(Event.venue_id == venue.id, _upcoming_filter(datetime.now()))
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


@localized_route("/<canton_slug:canton_slug>/")
def canton_page(canton_slug):
    # The canton_slug converter excludes reserved segments at routing time
    # (so /logout etc. keep their behavior), and static rules take
    # precedence anyway; unknown-but-valid slugs 404 here.
    info = canton_directory().get(canton_slug)
    if info is None:
        abort(404)
    events = (
        Event.query.options(joinedload(Event.venue))
        .filter(Event.venue_id.in_(info["venue_ids"]), _upcoming_filter(datetime.now()))
        .order_by(Event.date.asc())
        .all()
    )
    venues = (
        Venue.query.filter(Venue.id.in_(info["venue_ids"]))
        .order_by(Venue.name.asc())
        .all()
    )
    # Built once and used for both the <title>/<meta> pair and the JSON-LD, so
    # the two can't drift apart.
    page_title = _(
        "DIY & punk concerts %(in_canton)s", in_canton=canton_in(info["name"])
    )
    page_description = _(
        "%(num)d upcoming DIY, punk and underground shows %(in_canton)s — "
        "dates, venues, prices and accessibility info.",
        num=len(events),
        in_canton=canton_in(info["name"]),
    )
    return render_template(
        "canton.html",
        canton=info["name"],
        canton_slug=canton_slug,
        events=events,
        venues=venues,
        grouped_events=_group_events_by_date(events),
        page_title=page_title,
        page_description=page_description,
        intro_text=get_page_text("canton", canton_slug),
        canton_ld=collection_json_ld(
            page_title,
            page_description,
            canonical_url(),
            events,
        ),
    )


@localized_route("/genre/<genre_slug>/")
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
            _upcoming_filter(datetime.now()),
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
    page_title = _("%(genre)s concerts in Switzerland", genre=info["name"])
    page_description = _(
        "%(num)d upcoming %(genre)s shows in Switzerland — dates, "
        "venues, prices and accessibility info.",
        num=len(events),
        genre=info["name"],
    )
    return render_template(
        "genre.html",
        genre=info["name"],
        genre_slug=genre_slug,
        events=events,
        venues=venues,
        cantons=cantons,
        grouped_events=_group_events_by_date(events),
        page_title=page_title,
        page_description=page_description,
        intro_text=get_page_text("genre", genre_slug),
        genre_ld=collection_json_ld(
            page_title,
            page_description,
            canonical_url(),
            events,
        ),
    )


def _path_locale_cache_key(*_args, **_kwargs):
    # flask-caching passes the view args through; the request path already
    # encodes them, so the key only needs path + locale. Shared by the
    # archive and label pages.
    return f"page:{request.path}:{getattr(g, 'locale', DEFAULT_LOCALE)}"


@localized_route("/label/<label_slug>/")
@cache.cached(make_cache_key=_path_locale_cache_key, unless=_skip_calendar_cache)
def label_page(label_slug):
    # Label pages stay live even with no upcoming events (unlike genre
    # pages): promoters share the URL before their first show is posted.
    label = Label.query.filter_by(slug=label_slug).first_or_404()
    events = (
        Event.query.options(joinedload(Event.venue))
        .filter(Event.label_id == label.id, _upcoming_filter(datetime.now()))
        .order_by(Event.date.asc())
        .all()
    )
    # Label names are proper nouns, identical across locales — not wrapped in _().
    page_description = _(
        "Upcoming shows by %(label)s — dates, venues, prices and accessibility info.",
        label=label.name,
    )
    return render_template(
        "label_page.html",
        label=label,
        grouped_events=_group_events_by_date(events),
        page_title=label.name,
        page_description=page_description,
        label_ld=collection_json_ld(
            label.name,
            page_description,
            canonical_url(),
            events,
        ),
    )


# --- Calendar export --------------------------------------------------------

# A subscription is long-lived, so it looks further ahead than the calendar
# page's fixed three months.
ICS_HORIZON_MONTHS = 6


def _ics_response(body, filename=None):
    # mimetype only — Flask appends the charset itself, and passing it here
    # too produces a doubled "charset=utf-8; charset=utf-8".
    response = Response(body, mimetype="text/calendar")
    if filename:
        response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    response.headers["Cache-Control"] = "public, max-age=3600"
    return response


def _ics_feed_cache_key():
    # Feeds carry no page chrome, so unlike the HTML pages the key must not
    # include the locale — only the filters, normalized to their resolved form.
    args = request.args
    parts = [args.get(name, "") for name in ("canton", "genre", "label")]
    return "calendar_ics:" + ":".join(parts)


@bp.route("/calendar.ics")
@cache.cached(make_cache_key=_ics_feed_cache_key)
def calendar_ics():
    """Subscribable feed of upcoming shows, optionally filtered.

    ?canton=<slug>, ?genre=<slug> and ?label=<slug> resolve through the same
    directories the landing pages use; an unknown slug 404s rather than
    silently returning the unfiltered feed, so a shared subscription URL can
    never quietly widen into everything.
    """
    now = datetime.now()
    query = Event.query.options(joinedload(Event.venue)).filter(
        Event.date >= now,
        Event.date <= now + relativedelta(months=ICS_HORIZON_MONTHS),
    )
    name_parts = []

    canton_slug = request.args.get("canton")
    if canton_slug:
        info = canton_directory().get(canton_slug)
        if info is None:
            abort(404)
        query = query.filter(Event.venue_id.in_(info["venue_ids"]))
        name_parts.append(info["name"])

    genre_slug = request.args.get("genre")
    if genre_slug:
        info = genre_directory().get(genre_slug)
        if info is None:
            abort(404)
        query = query.filter(Event.parent_genres.like(f"%,{info['name']},%"))
        name_parts.append(info["name"])

    label_slug = request.args.get("label")
    if label_slug:
        label = Label.query.filter_by(slug=label_slug).first_or_404()
        query = query.filter(Event.label_id == label.id)
        name_parts.append(label.name)

    events = query.order_by(Event.date.asc()).all()
    name = "diytracker.ch"
    if name_parts:
        name += " · " + " · ".join(name_parts)
    body = build_calendar(
        events,
        name,
        _("DIY, punk and underground concerts in Switzerland."),
    )
    return _ics_response(body)


@bp.route("/events/<int:event_id>.ics")
def event_ics(event_id):
    event = (
        Event.query.options(joinedload(Event.venue))
        .filter_by(id=event_id)
        .first_or_404()
    )
    body = build_calendar(
        [event], event.name or event.acts or "diytracker.ch", description=None
    )
    filename = (slugify(event.name or event.acts or "event") or "event") + ".ics"
    return _ics_response(body, filename=filename)


# --- Search -----------------------------------------------------------------


@localized_route("/search")
# Every query is an uncached LIKE scan across events and venues; the limit is
# generous for a human typing and cheap insurance against a scripted sweep.
@limiter.limit("60 per hour")
def search():
    # Deliberately not cached: the query space is unbounded and the page cache
    # is on disk, so caching results would fill it with single-use entries.
    raw_query = request.args.get("q", "")
    query = normalise_query(raw_query)
    include_past = request.args.get("past") == "1"
    events = search_events(query, include_past=include_past) if query else []
    venues = search_venues(query) if query else []
    return render_template(
        "search.html",
        query=query,
        include_past=include_past,
        events=events,
        venues=venues,
        grouped_events=_group_events_by_date(events),
    )


@localized_route("/archive/")
@cache.cached(make_cache_key=_path_locale_cache_key, unless=_skip_calendar_cache)
def archive_index():
    # Not to be confused with /events/archive/, the scrape-detection
    # honeypot below — this is the real, visible archive.
    return render_template(
        "archive_index.html", directory=archive_directory(), datetime=datetime
    )


@localized_route("/archive/<int:year>/<int:month>/")
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


def _sitemap_entries(endpoint, changefreq, lastmod=None, **view_args):
    """One sitemap entry per locale variant of a localized page, all sharing
    the same alternate set. Locales are passed explicitly so the (cached)
    sitemap never inherits the requester's locale."""
    paths = localized_paths(endpoint, view_args)
    alternates = hreflang_entries(endpoint, view_args)
    return [
        {
            "loc": canonical_url(paths[loc]),
            "changefreq": changefreq,
            "lastmod": lastmod,
            "alternates": alternates,
        }
        for loc in SUPPORTED_LOCALES
    ]


@bp.route("/sitemap.xml")
@cache.cached()
def sitemap():
    # Localized pages appear once per locale, each carrying the full
    # hreflang alternate set. agb is excluded while its route still returns
    # a "WIP" placeholder.
    pages = []
    pages += _sitemap_entries("public.calendar_view", "daily")
    pages += _sitemap_entries("public.about", "monthly")
    pages += _sitemap_entries("public.venue_map", "weekly")
    for doc in ("impressum", "datenschutz"):
        pages += _sitemap_entries("public.legal", "yearly", doc=doc)

    for slug in sorted(canton_directory()):
        pages += _sitemap_entries("public.canton_page", "daily", canton_slug=slug)

    for slug in sorted(genre_directory()):
        pages += _sitemap_entries("public.genre_page", "daily", genre_slug=slug)

    for label in Label.query.order_by(Label.slug.asc()).all():
        pages += _sitemap_entries(
            "public.label_page",
            "daily",
            label.updated_at.date().isoformat() if label.updated_at else None,
            label_slug=label.slug,
        )

    # Archive pages follow the same two-year sitemap horizon as past events
    # below; older months stay reachable through the archive index.
    horizon = datetime.now() - relativedelta(years=2)
    pages += _sitemap_entries("public.archive_index", "monthly")
    for year, month_counts in archive_directory().items():
        for month, _count in month_counts:
            if datetime(year, month, 1) + relativedelta(months=1) < horizon:
                continue
            pages += _sitemap_entries(
                "public.archive_month", "monthly", year=year, month=month
            )

    # Past events stay listed for two years — they keep ranking for band and
    # venue queries — then age out of the sitemap (the pages themselves stay).
    events = Event.query.filter(Event.date >= horizon).order_by(Event.date.asc()).all()
    for event in events:
        pages += _sitemap_entries(
            "public.event_page",
            "weekly",
            event.updated_at.date().isoformat() if event.updated_at else None,
            event_id=event.id,
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
        pages += _sitemap_entries(
            "public.venue_page",
            "weekly",
            lastmod.date().isoformat() if lastmod else None,
            venue_id=venue.id,
        )
        if venue.id in accessibility:
            # Accessibility forms are not localized: single entry, no alternates.
            pages.append(
                {
                    "loc": canonical_url(
                        url_for("public.venue_accessibility", venue_id=venue.id)
                    ),
                    "changefreq": "monthly",
                    "lastmod": accessibility[venue.id].date().isoformat()
                    if accessibility[venue.id]
                    else None,
                    "alternates": [],
                }
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
            # Unbounded query space: every ?q= is a distinct URL with no value
            # to an index, and the results page carries noindex to match.
            "Disallow: /search",
            # One link per locale on every page, each redirecting to a page
            # that carries the set again — a multiplying space of
            # session-writing redirects with nothing to index. The switcher
            # links are rel="nofollow" to match.
            "Disallow: /set-language/",
            "",
            f"Sitemap: {canonical_url(url_for('public.sitemap'))}",
            "",
        ]
    )
    return Response(body, mimetype="text/plain")


@bp.route("/llms.txt")
@cache.cached()
def llms_txt():
    # Curated Markdown index for LLM crawlers/agents (https://llmstxt.org):
    # unlike the sitemap, this lists only the site's bounded, stable
    # sections — individual events churn too fast to belong here.
    cantons = sorted(canton_directory().items(), key=lambda kv: kv[1]["name"])
    genres = sorted(genre_directory().items(), key=lambda kv: kv[1]["name"])
    labels = Label.query.order_by(Label.name.asc()).all()
    body = render_template("llms.txt", cantons=cantons, genres=genres, labels=labels)
    return Response(body, mimetype="text/plain")


@bp.route(HONEYPOT_PATH)
def events_archive():
    # Scrape-detection honeypot (services/scrape_detection.py flags the hit
    # in its before_request hook). Render something plausible so scrapers
    # don't realise they've been spotted.
    return render_template("errors/404.html"), 200


@bp.route("/set-language/<lang>")
# A real visitor switches language a handful of times; anything hammering this
# route is a crawler walking the switcher links (see robots() above).
@limiter.limit("30 per hour")
def set_language(lang):
    if lang not in SUPPORTED_LOCALES:
        abort(404)
    session["lang"] = lang
    next_url = safe_redirect_target(request.args.get("next", ""))
    resp = redirect(next_url or url_for("public.calendar_view"))
    # For crawlers that reach the URL without having read robots.txt.
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    return resp


@bp.route("/kyuubi")
def kyuubi():
    return render_template("kyuubi.html")
