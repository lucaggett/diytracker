"""Application factory.

`create_app()` builds a fully wired Flask app and has no import-time side
effects: no dotenv loading, no DB access, no threads. Entrypoints own those
decisions — see the root-level app.py (gunicorn / dev server), manage.py
(CLI) and tests/conftest.py (test app).
"""

import os

from flask import Flask, g, render_template
from flask_babel import Babel, format_date
from flask_compress import Compress
from flask_wtf import CSRFProtect
from werkzeug.middleware.proxy_fix import ProxyFix

from dotenv import load_dotenv

from diytracker.config import Config
from diytracker.paths import (
    INSTANCE_DIR,
    ROOT,
    STATIC_DIR,
    TEMPLATES_DIR,
    ensure_runtime_dirs,
)
from diytracker.models import db
from diytracker.services.i18n import DEFAULT_LOCALE, SUPPORTED_LOCALES, select_locale
from diytracker.services.cache import cache
from diytracker.services.limits import limiter
from diytracker.services.scrape_detection import detector
from diytracker.services.seo import CantonSlugConverter, canonical_url, website_json_ld
from diytracker.utils import normalise_canton, parent_genres


def create_app(config: Config | None = None) -> Flask:
    if config is None:
        load_dotenv(ROOT / ".env")
        config = Config.from_env()

    ensure_runtime_dirs()

    # Explicit repo-anchored paths: the package module's location must not
    # decide where Flask looks for templates/static/instance (the SQLite DB
    # lives under instance_path, so a silent shift would boot against a fresh
    # empty DB).
    app = Flask(
        __name__,
        template_folder=str(TEMPLATES_DIR),
        static_folder=str(STATIC_DIR),
        instance_path=str(INSTANCE_DIR),
    )
    app.config.from_object(config)

    Compress(app)
    csrf = CSRFProtect(app)
    # nginx terminates TLS and proxies to gunicorn on localhost.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

    db.init_app(app)
    cache.init_app(app)
    limiter.init_app(app)
    detector.init_app(app)
    Babel(app, locale_selector=select_locale)

    # create_all() is idempotent and only creates missing tables (the project
    # has no Alembic); column additions live in migrations/.
    with app.app_context():
        db.create_all()
        # Lazy import: genre_catalog needs the models, which need an initialized db.
        from diytracker.services.genre_catalog import seed_genres

        seed_genres()

    app.jinja_env.globals["format_date"] = format_date
    app.jinja_env.globals["SUPPORTED_LOCALES"] = SUPPORTED_LOCALES
    app.jinja_env.globals["parent_genres"] = parent_genres
    app.jinja_env.globals["normalise_canton"] = normalise_canton
    app.jinja_env.globals["canonical_url"] = canonical_url
    app.jinja_env.globals["website_json_ld"] = website_json_ld

    @app.context_processor
    def _inject_footer_cantons():
        # Lazy import: cantons.py needs the models, which need an initialized db.
        from diytracker.services.cantons import top_cantons

        return {"footer_cantons": top_cantons}

    @app.context_processor
    def _inject_footer_genres():
        from diytracker.services.genres import top_genres

        return {"footer_genres": top_genres}

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

    @app.context_processor
    def _inject_current_user():
        # Templates only see the session; the header needs the promoter/admin
        # flags to decide which nav links to render.
        from flask import session

        from diytracker.models import Submitter

        def _lookup():
            if "_current_user" not in g:
                user_id = session.get("user_id")
                g._current_user = (
                    db.session.get(Submitter, user_id) if user_id else None
                )
            return g._current_user

        return {"current_user": _lookup}

    @app.errorhandler(404)
    def _handle_404(_err):
        return render_template("errors/404.html"), 404

    @app.errorhandler(500)
    def _handle_500(_err):
        return render_template("errors/500.html"), 500

    # Must be registered before the blueprints that use it in routes.
    app.url_map.converters["canton_slug"] = CantonSlugConverter

    from diytracker.blueprints.auth import bp as auth_bp
    from diytracker.blueprints.public import bp as public_bp
    from diytracker.blueprints.submissions import bp as submissions_bp
    from diytracker.blueprints.admin import bp as admin_bp
    from diytracker.blueprints.api import bp as api_bp
    from diytracker.blueprints.promoter import bp as promoter_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(public_bp)
    app.register_blueprint(submissions_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(promoter_bp)

    # /api/ingest authenticates with a bearer token, not a session cookie, so
    # browser CSRF doesn't apply (and external pushers can't obtain a CSRF token).
    csrf.exempt(app.view_functions["api.ingest"])

    return app
