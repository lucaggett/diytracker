from flask_caching import Cache

from diytracker.paths import CACHE_DIR

CACHE_TIMEOUT_SECONDS = 60 * 60

# Filesystem-backed so all gunicorn workers share one cache and bust_cache()
# takes effect everywhere (SimpleCache was per-process: a write handled by one
# worker left the other workers serving stale pages for up to an hour).
_CACHE_DIR = str(CACHE_DIR)

cache = Cache(
    config={
        "CACHE_TYPE": "FileSystemCache",
        "CACHE_DIR": _CACHE_DIR,
        "CACHE_DEFAULT_TIMEOUT": CACHE_TIMEOUT_SECONDS,
        "CACHE_THRESHOLD": 1000,
    }
)


def bust_cache():
    cache.clear()
