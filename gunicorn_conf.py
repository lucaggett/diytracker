# /diytracker/gunicorn_conf.py
bind = '127.0.0.1:8080'
worker_class = 'sync'
loglevel = 'debug'
accesslog = 'logs/access_log_diytracker'
acceslogformat ="%(h)s %(l)s %(u)s %(t)s %(r)s %(s)s %(b)s %(f)s %(a)s"
errorlog =  'logs/error_log_diytracker'