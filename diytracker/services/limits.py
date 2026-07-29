from flask import request, session
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# In-memory storage is per-process; with 4 gunicorn workers the effective
# limit is up to 4x the configured rate, which is fine for brute-force
# protection. Relies on ProxyFix so get_remote_address sees the real client IP.


def _exempt_from_default_limit():
    # Static files (nginx serves these in production, so this only matters in
    # dev) and logged-in users are never the traffic the ceiling is aimed at.
    return request.endpoint == "static" or "user_id" in session


# The default is a ceiling, not a throttle: no human browsing the site comes
# near 120 requests a minute, but a crawler walking every filter combination
# does. Routes that need a real throttle carry their own @limiter.limit.
limiter = Limiter(
    key_func=get_remote_address,
    storage_uri="memory://",
    default_limits=["120 per minute"],
    default_limits_per_method=False,
    default_limits_exempt_when=_exempt_from_default_limit,
)
