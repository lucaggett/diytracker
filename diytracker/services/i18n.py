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
