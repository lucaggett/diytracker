"""Admin-editable intro texts for the canton and genre landing pages.

Texts are PageText rows keyed (kind, key, locale); rendering falls back to
the default locale so a page written only in German still shows something
on /fr/..., and shows nothing at all when no row exists.
"""

from flask import g

from diytracker.models import PageText
from diytracker.services.i18n import DEFAULT_LOCALE
from diytracker.services.seo import slugify
from diytracker.utils import CANTONS


def get_page_text(kind, key, locale=None):
    """Text for (kind, key) in `locale` (default: current request locale),
    falling back to the default locale; None when neither exists."""
    locale = locale or g.get("locale", DEFAULT_LOCALE)
    rows = PageText.query.filter_by(kind=kind, key=key).filter(
        PageText.locale.in_({locale, DEFAULT_LOCALE})
    )
    by_locale = {row.locale: row.text for row in rows}
    # Blank rows are never stored (the admin form deletes blanked locales),
    # so the or-fallback can't mask an intentionally empty translation.
    return by_locale.get(locale) or by_locale.get(DEFAULT_LOCALE)


def valid_page_text_keys():
    """{kind: {slug, ...}} of every page that may carry an intro text: all 26
    cantons and the parent genres (Other has no landing page). Independent of
    whether the page currently has upcoming events, so texts can be authored
    ahead of time."""
    from diytracker.services.genres import GENRE_SLUGS

    return {
        "canton": {slugify(name) for name in CANTONS.values()},
        "genre": set(GENRE_SLUGS),
    }
