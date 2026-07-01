from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# In-memory storage is per-process; with 4 gunicorn workers the effective
# limit is up to 4x the configured rate, which is fine for brute-force
# protection. Relies on ProxyFix so get_remote_address sees the real client IP.
limiter = Limiter(key_func=get_remote_address, storage_uri='memory://')
