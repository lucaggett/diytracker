"""WSGI / dev-server entrypoint.

Keeps the long-standing gunicorn target `app:app` working unchanged
(deploy/diytracker.service: ExecStart ... gunicorn -c gunicorn_conf.py app:app).
The application itself is built by the factory in diytracker/app.py.
"""

import os

from diytracker.app import create_app
from diytracker.services.analytics import start_stats_scheduler
from diytracker.services.scraper import start_auto_scheduler

app = create_app()

# Only one process may run the background jobs (scrape scheduler, analytics
# stats refresh). Under gunicorn the post_fork hook in gunicorn_conf.py sets
# this for the first worker only; for the dev server or a standalone run, set
# ENABLE_SCRAPER=1 yourself.
if os.environ.get("ENABLE_SCRAPER") == "1":
    start_auto_scheduler(app)
    start_stats_scheduler(app)

if __name__ == "__main__":
    app.run(debug=True, port=5001)
