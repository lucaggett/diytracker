import os
from datetime import timedelta

from flask_compress import Compress

from dotenv import load_dotenv
from flask import Flask, g, render_template

from models import db
from services.i18n import (
    DEFAULT_LOCALE,
    SUPPORTED_LOCALES,
    gettext,
    select_locale,
)
from services.cache import cache
from services.scraper import start_auto_scheduler
from services.uploads import ALLOWED_EXTENSIONS, UPLOAD_FOLDER
from utils import parent_genres

load_dotenv()

for d in ('logs', 'static/uploads', 'instance', 'instance/cache'):
    os.makedirs(d, exist_ok=True)

app = Flask(__name__)

app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///events.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = os.environ['SECRET_KEY']
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=7)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['ALLOWED_EXTENSIONS'] = ALLOWED_EXTENSIONS
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 31536000
app.config['COMPRESS_ALGORITHM'] = ['br', 'gzip']
Compress(app)

app.config['BABEL_DEFAULT_LOCALE'] = DEFAULT_LOCALE
app.config['BABEL_SUPPORTED_LOCALES'] = list(SUPPORTED_LOCALES)
app.config['BABEL_TRANSLATION_DIRECTORIES'] = 'translations'

db.init_app(app)
cache.init_app(app)

with app.app_context():
    db.create_all()

try:
    from flask_babel import Babel, format_date
    babel = Babel(app, locale_selector=select_locale)
except ImportError:
    app.jinja_env.globals['_'] = gettext
    app.jinja_env.globals['gettext'] = gettext

    def format_date(date, _fmt=None):
        return date.strftime('%a · %d.%m.%Y')

app.jinja_env.globals['format_date'] = format_date

app.jinja_env.globals['SUPPORTED_LOCALES'] = SUPPORTED_LOCALES
app.jinja_env.globals['parent_genres'] = parent_genres


@app.context_processor
def _static_version():
    css_path = os.path.join(app.static_folder, 'css', 'output.css')
    try:
        v = int(os.path.getmtime(css_path))
    except OSError:
        v = 0
    return {'css_version': v}


@app.before_request
def _bind_locale_to_g():
    try:
        g.locale = select_locale()
    except RuntimeError:
        g.locale = DEFAULT_LOCALE


@app.context_processor
def _inject_locale():
    return {'current_locale': getattr(g, 'locale', DEFAULT_LOCALE)}


@app.errorhandler(404)
def _handle_404(_err):
    return render_template('errors/404.html'), 404


@app.errorhandler(500)
def _handle_500(_err):
    return render_template('errors/500.html'), 500


from blueprints.auth import bp as auth_bp
from blueprints.public import bp as public_bp
from blueprints.submissions import bp as submissions_bp
from blueprints.admin import bp as admin_bp
from blueprints.api import bp as api_bp

app.register_blueprint(auth_bp)
app.register_blueprint(public_bp)
app.register_blueprint(submissions_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(api_bp)

start_auto_scheduler(app)


if __name__ == '__main__':
    app.run(debug=True, port=5001)
