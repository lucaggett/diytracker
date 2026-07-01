# /diytracker/gunicorn_conf.py
import os

bind = '127.0.0.1:8080'
worker_class = 'sync'
workers = 4
loglevel = 'info'
accesslog = 'logs/access_log_diytracker'
access_log_format = "%(h)s %(l)s %(u)s %(t)s %(r)s %(s)s %(b)s %(f)s %(a)s"
errorlog = 'logs/error_log_diytracker'


def post_fork(server, worker):
    # Exactly one worker runs the background scrape scheduler (app.py gates
    # on ENABLE_SCRAPER). worker.age is 1 for the first worker spawned; note
    # that if that worker dies, the scheduler is gone until the next restart.
    if worker.age == 1:
        os.environ['ENABLE_SCRAPER'] = '1'
