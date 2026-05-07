from flask_caching import Cache

CACHE_TIMEOUT_SECONDS = 60 * 60

cache = Cache(config={
    'CACHE_TYPE': 'FileSystemCache',
    'CACHE_DIR': 'instance/cache',
    'CACHE_DEFAULT_TIMEOUT': CACHE_TIMEOUT_SECONDS,
    'CACHE_THRESHOLD': 1000,
})


def bust_cache():
    cache.clear()
