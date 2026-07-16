"""SEO helpers: slugs, canonical URLs, and schema.org JSON-LD builders.

The JSON-LD builders return plain dicts; templates render them with
`{{ ld | tojson }}`, which escapes `<` and keeps the output valid inside a
<script type="application/ld+json"> tag.
"""

import re
import unicodedata
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from flask import current_app, request, url_for
from werkzeug.routing import BaseConverter

from diytracker.services.i18n import DEFAULT_LOCALE, SUPPORTED_LOCALES

ZURICH = ZoneInfo("Europe/Zurich")

# Swiss convention: umlauts transliterate to two letters (Zürich → zuerich),
# unlike the plain accent-stripping applied to everything else (Genève → geneve).
_SWISS_TRANSLIT = str.maketrans(
    {"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "ae", "Ö": "oe", "Ü": "ue", "ß": "ss"}
)

# Slugs that may never become canton landing pages: every static first path
# segment of the app, the locale prefixes used by the legal pages and the
# localized public routes, and the legal document names (so /fr/impressum
# can only ever match the legal rule, never a locale-prefixed canton rule).
RESERVED_SLUGS = frozenset(
    {
        "about",
        "accessibility",
        "admin",
        "agb",
        "archive",
        "api",
        "datenschutz",
        "events",
        "genre",
        "impressum",
        "kyuubi",
        "label",
        "login",
        "logout",
        "map",
        "promoter",
        "queue",
        "robots.txt",
        "set-language",
        "set-password",
        "sitemap.xml",
        "static",
        "submit",
        "venues",
    }
    | set(SUPPORTED_LOCALES)
)


class CantonSlugConverter(BaseConverter):
    """URL converter for /<canton-slug>/ landing pages: lowercase slugs only,
    with reserved segments excluded at routing time so paths like /logout
    keep their original behavior (405, honeypot, ...) instead of being
    slash-redirected into the canton route."""

    regex = (
        r"(?!(?:" + "|".join(re.escape(s) for s in sorted(RESERVED_SLUGS)) + r")$)"
        r"[a-z0-9-]+"
    )


_EVENT_STATUS_URLS = {
    "scheduled": "https://schema.org/EventScheduled",
    "cancelled": "https://schema.org/EventCancelled",
    "postponed": "https://schema.org/EventPostponed",
}

# Free-text ticket prices that mean "free entry / donation".
_FREE_PRICE_RE = re.compile(
    r"kollekte|gratis|free|frei|donation|pay what you", re.IGNORECASE
)
_PRICE_RE = re.compile(r"\d+(?:[.,]\d{1,2})?")


def slugify(value):
    """Slug for URLs, Swiss-style: Zürich → zuerich, Genève 8 → geneve-8."""
    value = (value or "").translate(_SWISS_TRANSLIT)
    value = unicodedata.normalize("NFKD", value)
    value = value.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "-", value).strip("-")


def canonical_url(path=None):
    """Absolute canonical URL for `path` (default: current request path).

    Uses CANONICAL_HOST when configured (production) so the canonical host
    and scheme never depend on incoming headers; falls back to the request
    root for dev/tests. Query strings are deliberately dropped.
    """
    base = (current_app.config.get("CANONICAL_HOST") or "").rstrip("/")
    if not base:
        base = request.url_root.rstrip("/")
    if path is None:
        path = request.path
    return base + path


def localized_paths(endpoint=None, view_args=None):
    """{locale: relative URL} for the current (or given) page; {} when the
    page has no locale variants.

    Passes an explicit lang_prefix/lang per locale so the result never
    depends on the requester's own locale (the sitemap is cached and must
    not inherit it)."""
    if endpoint is None:
        rule = request.url_rule
        if rule is None:  # 404/500 pages
            return {}
        endpoint, view_args = rule.endpoint, request.view_args
    args = dict(view_args or {})
    args.pop("lang_prefix", None)
    if current_app.url_map.is_endpoint_expecting(endpoint, "lang_prefix"):
        return {
            loc: url_for(
                endpoint,
                **args,
                lang_prefix=None if loc == DEFAULT_LOCALE else loc,
            )
            for loc in SUPPORTED_LOCALES
        }
    # Legal pages carry their locale in <lang> instead of a prefix.
    if endpoint == "public.legal":
        return {
            loc: url_for(endpoint, **{**args, "lang": loc}) for loc in SUPPORTED_LOCALES
        }
    return {}


def hreflang_entries(endpoint=None, view_args=None):
    """[(hreflang, absolute URL)] for all locales plus x-default (= the
    default locale's URL); [] when the page is not localizable."""
    paths = localized_paths(endpoint, view_args)
    if not paths:
        return []
    entries = [(loc, canonical_url(paths[loc])) for loc in SUPPORTED_LOCALES]
    entries.append(("x-default", canonical_url(paths[DEFAULT_LOCALE])))
    return entries


def parse_price(raw):
    """Numeric price from the free-text ticket_price column.

    Explicit free/donation wording maps to 0 (schema.org wants a price, and
    the spec says free/Kollekte shows are price 0). Ranges like "8-25" yield
    the lower bound. Returns None when nothing numeric can be extracted.
    """
    raw = (raw or "").strip()
    if not raw:
        return None
    match = _PRICE_RE.search(raw)
    if match:
        return float(match.group().replace(",", "."))
    if _FREE_PRICE_RE.search(raw):
        return 0.0
    return None


def parse_acts(raw):
    """Split the free-text acts column (newline-, pipe- or comma-separated)
    into a deduplicated list of act names."""
    seen = {}
    for part in re.split(r"[\n|,]", raw or ""):
        name = part.strip()
        if name:
            seen.setdefault(name.casefold(), name)
    return list(seen.values())


def _flyer_url(flyer):
    """Absolute URL for a flyer path stored root-relative ('static/uploads/x')."""
    if not flyer:
        return None
    if "://" in flyer:
        return flyer
    return canonical_url("/" + flyer.lstrip("/"))


def parse_swiss_coords(coords):
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


def _postal_address(venue):
    address = {"@type": "PostalAddress", "addressCountry": "CH"}
    if venue.address:
        address["streetAddress"] = venue.address
    if venue.city:
        address["addressLocality"] = venue.city
    if venue.canton:
        address["addressRegion"] = venue.canton
    if venue.plz:
        address["postalCode"] = venue.plz
    return address


def _geo(coords):
    return {"@type": "GeoCoordinates", "latitude": coords[0], "longitude": coords[1]}


def event_json_ld(event):
    venue = event.venue
    start = datetime.combine(event.date.date(), event.doors, tzinfo=ZURICH)

    location = {
        "@type": "Place",
        "name": venue.name,
        "address": _postal_address(venue),
    }
    coords = parse_swiss_coords(venue.coords)
    if coords:
        location["geo"] = _geo(coords)

    ld = {
        "@context": "https://schema.org",
        "@type": "MusicEvent",
        "name": event.name or event.acts or "Event",
        "startDate": start.isoformat(),
        "eventStatus": _EVENT_STATUS_URLS.get(
            event.status, _EVENT_STATUS_URLS["scheduled"]
        ),
        "url": canonical_url(url_for("public.event_page", event_id=event.id)),
        "location": location,
    }

    ld["endDate"] = (event.end_date or event.date.date()).isoformat()

    # created_at is nullable (backfilled), updated_at never is — one of the
    # two always gives an honest lower bound for when the listing went live.
    valid_from = event.created_at or event.updated_at
    ld["validFrom"] = valid_from.replace(tzinfo=timezone.utc).isoformat()

    performers = parse_acts(event.acts)
    if performers:
        ld["performer"] = [{"@type": "MusicGroup", "name": name} for name in performers]

    price = parse_price(event.ticket_price)
    ticket_is_safe = (event.ticket_link or "").startswith(("http://", "https://"))
    if price is not None or ticket_is_safe:
        offer = {"@type": "Offer", "priceCurrency": "CHF"}
        if price is not None:
            offer["price"] = price
            offer["availability"] = "https://schema.org/InStock"
        if ticket_is_safe:
            offer["url"] = event.ticket_link
        ld["offers"] = offer

    flyer = _flyer_url(event.flyer)
    if flyer:
        ld["image"] = flyer

    if event.description:
        text = " ".join(event.description.split())
        ld["description"] = text[:300]
    else:
        # No free-text description: fall back to genre(s) and lineup so the
        # listing still has something for search engines to show.
        fallback_parts = []
        if event.genre:
            fallback_parts.append(event.genre)
        if performers:
            fallback_parts.append(", ".join(performers))
        if fallback_parts:
            ld["description"] = " · ".join(fallback_parts)

    return ld


# VenueAccessibility columns worth exposing as schema.org amenityFeature,
# with their human-readable feature names.
_AMENITY_FEATURES = (
    ("step_free_entrance", "Step-free entrance"),
    ("step_free_interior", "Step-free interior"),
    ("accessible_toilet", "Wheelchair accessible toilet"),
    ("wheelchair_spaces", "Dedicated wheelchair spaces"),
    ("hearing_loop", "Hearing loop"),
    ("quiet_space", "Quiet room"),
    ("earplugs_available", "Free earplugs"),
    ("accessible_parking", "Accessible parking"),
    ("gender_neutral_toilets", "Gender-neutral toilets"),
    ("seating_areas", "Seating areas"),
    ("guide_dogs_welcome", "Guide dogs welcome"),
)


def venue_json_ld(venue, coords=None):
    ld = {
        "@context": "https://schema.org",
        "@type": "MusicVenue",
        "name": venue.name,
        "url": canonical_url(url_for("public.venue_page", venue_id=venue.id)),
        "address": _postal_address(venue),
    }
    if coords:
        ld["geo"] = _geo(coords)

    info = venue.accessibility
    if info:
        features = [
            {
                "@type": "LocationFeatureSpecification",
                "name": label,
                "value": getattr(info, column) == "yes",
            }
            for column, label in _AMENITY_FEATURES
            if getattr(info, column) in ("yes", "partial")
        ]
        if features:
            ld["amenityFeature"] = features

    return ld


def website_json_ld():
    return {
        "@context": "https://schema.org",
        "@type": "WebSite",
        "name": "diytracker.ch",
        "url": canonical_url("/"),
        "description": (
            "DIY, punk and underground concerts in Switzerland — "
            "shows, festivals and venues."
        ),
    }
