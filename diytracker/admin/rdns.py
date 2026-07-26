"""Reverse-DNS lookups for the traffic views, cached in the database.

The hosts breakdown wants a name behind each IP ("this many bot IPs are all
AWS"), but PTR lookups are network round trips: doing them inline would stall
the TUI for as long as the slowest resolver takes, and doing them again on
every refresh would re-ask the same questions all day.

So: answers live in IpHostname, misses are cached too (hostname NULL means
"asked, no PTR" — otherwise every refresh re-probes the unresolvable half of
the internet), and a single call resolves at most MAX_LOOKUPS addresses. What
doesn't fit is simply not resolved this time; callers render it as pending and
it fills in on the next refresh.

The budget plus the thread pool are the real bound on how long a call takes:
gethostbyaddr goes through the platform resolver, which honours its own
timeout (/etc/resolv.conf) rather than Python's socket timeout.
"""

import socket
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from diytracker.models import IpHostname, db, utcnow

MAX_AGE = timedelta(days=30)  # a PTR record rarely moves faster than this
MAX_LOOKUPS = 200  # per call; the rest waits for the next refresh
WORKERS = 16


def _lookup(ip):
    """PTR name for *ip*, or None. Never raises."""
    try:
        return socket.gethostbyaddr(ip)[0]
    except (OSError, socket.herror, socket.gaierror, UnicodeError):
        return None


def cached(ips):
    """{ip: hostname or None} for the entries already in the cache."""
    ips = list(ips)
    if not ips:
        return {}
    rows = IpHostname.query.filter(IpHostname.ip.in_(ips)).all()
    return {row.ip: row.hostname for row in rows}


def resolve_many(ips, max_lookups=None):
    """{ip: hostname or None} for *ips*, resolving what the cache lacks.

    *ips* is taken in caller order, so pass the busiest addresses first —
    they are the ones worth spending the lookup budget on. Addresses beyond
    the budget are left out of the result entirely (not cached as misses),
    which is how callers tell "no PTR" from "not asked yet".

    Needs an app context; commits its own transaction.
    """
    # Read the budget at call time, not as a default argument: bound at import
    # it would ignore anyone (a test, a caller) setting MAX_LOOKUPS.
    if max_lookups is None:
        max_lookups = MAX_LOOKUPS
    ips = list(dict.fromkeys(ips))  # de-dupe, keep order
    if not ips:
        return {}

    rows = {row.ip: row for row in IpHostname.query.filter(IpHostname.ip.in_(ips))}
    fresh_after = utcnow() - MAX_AGE
    known = {
        ip: row.hostname
        for ip, row in rows.items()
        if row.checked_at and row.checked_at > fresh_after
    }

    stale = [ip for ip in ips if ip not in known][:max_lookups]
    if not stale:
        return known

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        found = dict(zip(stale, pool.map(_lookup, stale)))

    now = utcnow()
    for ip, hostname in found.items():
        row = rows.get(ip)
        if row is None:
            row = IpHostname(ip=ip)
            db.session.add(row)
        row.hostname = hostname
        row.checked_at = now
    db.session.commit()

    known.update(found)
    return known
