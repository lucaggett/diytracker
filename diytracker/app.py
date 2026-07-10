import os
from datetime import timedelta

from flask_compress import Compress
from flask_wtf import CSRFProtect
from werkzeug.middleware.proxy_fix import ProxyFix

from dotenv import load_dotenv
from flask import Flask, g, render_template

from diytracker.paths import (
    INSTANCE_DIR,
    ROOT,
    STATIC_DIR,
    TEMPLATES_DIR,
    TRANSLATIONS_DIR,
    ensure_runtime_dirs,
)
from diytracker.models import db
from diytracker.services.i18n import (
    DEFAULT_LOCALE,
    SUPPORTED_LOCALES,
    gettext,
    select_locale,
)
from diytracker.services.cache import cache
from diytracker.services.limits import limiter
from diytracker.services.scrape_detection import detector
from diytracker.services.seo import CitySlugConverter, canonical_url, website_json_ld
from diytracker.services.scraper import start_auto_scheduler
from diytracker.services.uploads import ALLOWED_EXTENSIONS, UPLOAD_FOLDER
from diytracker.utils import normalise_canton, parent_genres

load_dotenv(ROOT / ".env")

ensure_runtime_dirs()

# Explicit repo-anchored paths: the package module's location must not decide
# where Flask looks for templates/static/instance (the SQLite DB lives under
# instance_path, so a silent shift would boot against a fresh empty DB).
app = Flask(
    __name__,
    template_folder=str(TEMPLATES_DIR),
    static_folder=str(STATIC_DIR),
    instance_path=str(INSTANCE_DIR),
)

app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
    "DATABASE_URI", "sqlite:///events.db"
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SECRET_KEY"] = os.environ["SECRET_KEY"]
# Shared token for POST /api/ingest (external event sources). Unset = endpoint disabled.
app.config["INGEST_TOKEN"] = os.environ.get("INGEST_TOKEN")
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=7)
# Secure works over http://localhost in modern browsers, so dev logins are fine.
app.config["SESSION_COOKIE_SECURE"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["ALLOWED_EXTENSIONS"] = ALLOWED_EXTENSIONS
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 31536000
# Canonical origin for <link rel="canonical">, og:url and JSON-LD URLs
# (e.g. "https://diytracker.ch"). Empty = fall back to the request host
# (dev/tests); production must set it so canonicals don't depend on headers.
app.config["CANONICAL_HOST"] = os.environ.get("CANONICAL_HOST", "")
app.config["COMPRESS_ALGORITHM"] = ["br", "gzip"]
Compress(app)
csrf = CSRFProtect(app)
# nginx terminates TLS and proxies to gunicorn on localhost.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

app.config["BABEL_DEFAULT_LOCALE"] = DEFAULT_LOCALE
app.config["BABEL_SUPPORTED_LOCALES"] = list(SUPPORTED_LOCALES)
app.config["BABEL_TRANSLATION_DIRECTORIES"] = str(TRANSLATIONS_DIR)

db.init_app(app)
cache.init_app(app)
limiter.init_app(app)
detector.init_app(app)

with app.app_context():
    db.create_all()

try:
    from flask_babel import Babel, format_date

    babel = Babel(app, locale_selector=select_locale)
except ImportError:
    app.jinja_env.globals["_"] = gettext
    app.jinja_env.globals["gettext"] = gettext

    def format_date(date, _fmt=None):
        return date.strftime("%a · %d.%m.%Y")


app.jinja_env.globals["format_date"] = format_date

app.jinja_env.globals["SUPPORTED_LOCALES"] = SUPPORTED_LOCALES
app.jinja_env.globals["parent_genres"] = parent_genres
app.jinja_env.globals["normalise_canton"] = normalise_canton
app.jinja_env.globals["canonical_url"] = canonical_url
app.jinja_env.globals["website_json_ld"] = website_json_ld


@app.context_processor
def _inject_footer_cities():
    # Lazy import: cities.py needs the models, which need an initialized db.
    from diytracker.services.cities import top_cities

    return {"footer_cities": top_cities}


@app.context_processor
def _static_version():
    css_path = os.path.join(app.static_folder, "css", "output.css")
    try:
        v = int(os.path.getmtime(css_path))
    except OSError:
        v = 0
    return {"css_version": v}


@app.before_request
def _bind_locale_to_g():
    try:
        g.locale = select_locale()
    except RuntimeError:
        g.locale = DEFAULT_LOCALE


@app.context_processor
def _inject_locale():
    return {"current_locale": getattr(g, "locale", DEFAULT_LOCALE)}


@app.errorhandler(404)
def _handle_404(_err):
    return render_template("errors/404.html"), 404


@app.errorhandler(500)
def _handle_500(_err):
    return render_template("errors/500.html"), 500


# Must be registered before the blueprints that use it in routes.
app.url_map.converters["city_slug"] = CitySlugConverter

from diytracker.blueprints.auth import bp as auth_bp
from diytracker.blueprints.public import bp as public_bp
from diytracker.blueprints.submissions import bp as submissions_bp
from diytracker.blueprints.admin import bp as admin_bp
from diytracker.blueprints.api import bp as api_bp

app.register_blueprint(auth_bp)
app.register_blueprint(public_bp)
app.register_blueprint(submissions_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(api_bp)

# /api/ingest authenticates with a bearer token, not a session cookie, so
# browser CSRF doesn't apply (and external pushers can't obtain a CSRF token).
csrf.exempt(app.view_functions["api.ingest"])

# Only one process may run the scrape scheduler. Under gunicorn the
# post_fork hook in gunicorn_conf.py sets this for the first worker only;
# for the dev server or a standalone run, set ENABLE_SCRAPER=1 yourself.
if os.environ.get("ENABLE_SCRAPER") == "1":
    start_auto_scheduler(app)


if __name__ == "__main__":
    app.run(debug=True, port=5001)
