# /diytracker/gunicorn_conf.py
import os
from pathlib import Path

# gunicorn executes this file before the project root is on sys.path, so the
# log paths are anchored on __file__ instead of importing diytracker.paths.
_ROOT = Path(__file__).resolve().parent

bind = "127.0.0.1:8080"
worker_class = "sync"
workers = 4
loglevel = "info"
accesslog = str(_ROOT / "logs" / "access_log_diytracker")
access_log_format = "%(h)s %(l)s %(u)s %(t)s %(r)s %(s)s %(b)s %(f)s %(a)s"
errorlog = str(_ROOT / "logs" / "error_log_diytracker")


def post_fork(server, worker):  # noqa: ARG001 - gunicorn hook signature
    # Exactly one worker runs the background jobs — scrape scheduler and
    # analytics stats refresh (app.py gates on ENABLE_SCRAPER). worker.age is
    # 1 for the first worker spawned; note that if that worker dies, the
    # schedulers are gone until the next restart.
    if worker.age == 1:
        os.environ["ENABLE_SCRAPER"] = "1"
