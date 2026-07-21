from flask import abort, g, request, session

SUPPORTED_LOCALES = ("de", "fr", "it", "en")
DEFAULT_LOCALE = "de"

try:
    from flask_babel import gettext as _babel_gettext  # type: ignore

    HAS_BABEL = True
except ImportError:
    HAS_BABEL = False

    def _babel_gettext(s, **kwargs):
        return s % kwargs if kwargs else s


def gettext(s, **kwargs):
    return _babel_gettext(s, **kwargs)


def canton_in(name):
    """The localized "in <canton>" phrase for a canonical CANTONS name.

    The preposition varies per canton and per language ("im Aargau" but "in
    der Waadt"; "à Genève" but "dans le canton de Vaud"), so the landing-page
    strings interpolate this whole phrase as %(in_canton)s rather than putting
    a fixed preposition in front of a translated name.
    """
    from diytracker.utils import CANTON_CODE_BY_NAME, CANTON_LOCATIVE

    code = CANTON_CODE_BY_NAME.get(name)
    phrase = CANTON_LOCATIVE.get(code, {}).get(current_locale())
    # Unknown canton, or a locale the table doesn't cover: "in <translated>"
    # is wrong for a handful of cantons but never unintelligible.
    return phrase or f"in {gettext(name)}"


def current_locale():
    """The active locale, usable outside a request context (falls back to the
    default rather than raising, so CLI/test callers work)."""
    try:
        locale = g.get("locale") or g.get("url_locale")
    except RuntimeError:  # no application/request context
        return DEFAULT_LOCALE
    if locale in SUPPORTED_LOCALES:
        return locale
    try:
        return select_locale()
    except RuntimeError:
        return DEFAULT_LOCALE


def select_locale():
    # A locale carried in the URL (/fr/..., set by the public blueprint's
    # url_value_preprocessor) always wins over the session and headers, so
    # each localized URL renders its own language regardless of cookies.
    url_locale = g.get("url_locale")
    if url_locale in SUPPORTED_LOCALES:
        return url_locale
    lang = session.get("lang")
    if lang in SUPPORTED_LOCALES:
        return lang
    best = request.accept_languages.best_match(list(SUPPORTED_LOCALES))
    return best or DEFAULT_LOCALE


def validate_lang(lang):
    if lang not in SUPPORTED_LOCALES:
        abort(404)
    g.url_locale = lang
    g.locale = lang
