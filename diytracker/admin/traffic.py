"""Traffic analysis: behavioural bot inference and event view distribution.

Everything is distilled from the access logs already on disk (nginx in
production, gunicorn's own log in dev) plus the ScrapeSuspect rows written by
services/scrape_detection.py — there is deliberately no tracking pixel or
client-side collection. Per-IP behaviour over the log window is scored
against signals that combined-log lines can actually support; header-based
signals (Accept-Language, client hints) only exist in ScrapeSuspect rows,
which get merged in when present. Static assets never appear in the nginx
log (access_log off), so asset-based signals are off the table too.

Weights and the bot threshold are calibrated against scrape_detection.py
(FLAG_THRESHOLD) and deploy/traffic_audit.sh's "estimated real users"
heuristic, so the numbers here agree with the existing tooling.
"""

import json
import re
import statistics
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import func

from diytracker.admin.core import ACCESS_LOG, AdminError, require_schema
from diytracker.models import Event, EventDailyViews, ScrapeSuspect, db
from diytracker.services.analytics import (
    _build_valid_dates,
    _date_range,
    _iter_filtered_lines,
    find_log_files,
)

TIMEFRAMES = {
    "7d": "last 7 days",
    "30d": "last 30 days",
    "all": "all logs",
}

BOT_THRESHOLD = 4.0  # matches scrape_detection.FLAG_THRESHOLD
SUSPICIOUS_THRESHOLD = 2.0

# nginx COMBINED: ip ident user [ts] "req" status size "ref" "ua"
_NGINX_RE = re.compile(
    r"^(?P<ip>\S+) \S+ \S+ \[(?P<ts>[^\]]+)\] "
    r'"(?P<method>\S+) (?P<path>\S+)[^"]*" (?P<status>\d{3}) \S+ '
    r'"(?P<ref>[^"]*)" "(?P<ua>[^"]*)"'
)
# gunicorn's access_log_format writes the same fields without quotes; every
# field before the UA is a single token, so the UA is the rest of the line.
_GUNICORN_RE = re.compile(
    r"^(?P<ip>\S+) \S+ \S+ \[(?P<ts>[^\]]+)\] "
    r"(?P<method>\S+) (?P<path>\S+) HTTP/\S+ (?P<status>\d{3}) \S+ "
    r"(?P<ref>\S+) (?P<ua>.*)$"
)

# Named crawlers that declare themselves in the UA. Self-reported, so they
# get their own bucket instead of counting as stealth bots.
_CRAWLER_RE = re.compile(
    r"bot|crawl|spider|slurp|preview|facebookexternalhit|gptbot|claudebot"
    r"|ccbot|semrush|ahrefs|awario|bytespider|petal|amazonbot",
    re.IGNORECASE,
)
# HTTP libraries, scanners and headless clients: scripted, not declared.
_SCRIPTED_RE = re.compile(
    r"python|curl|wget|scrapy|go-http|okhttp|aiohttp|httpx|httpclient"
    r"|libwww|java/|zgrab|headless|phantomjs|scan|monitor",
    re.IGNORECASE,
)
# Exploit probes, ported from traffic_audit.sh's probrx.
_PROBE_RE = re.compile(
    r"wp-login|wp-admin|xmlrpc\.php|\.php$|\.env|\.git|phpmyadmin|/cgi-bin"
    r"|\.aws|/vendor/|/config\.|/backup|/actuator|/\.ssh",
    re.IGNORECASE,
)
_EVENT_PAGE_RE = re.compile(r"^/events/(\d+)/?$")
_HONEYPOT_PREFIX = "/events/archive"  # scrape_detection's trap path

_SCHEMA_HINT = "Run the release's one-shot migration script first."

_SPARK_CHARS = " ▁▂▃▄▅▆▇█"
_SPARK_DAYS = 30  # trend column width cap


@dataclass
class DayRow:
    date: str  # ISO
    requests: int
    unique_ips: int
    est_humans: int


@dataclass
class IpProfile:
    ip: str
    bucket: str  # crawler | bot | suspicious | human | unclear
    score: float
    signals: list = field(default_factory=list)  # "name (+weight)" strings
    requests: int = 0
    first_seen: str = ""
    last_seen: str = ""
    ua_sample: str = ""
    n_uas: int = 1
    top_paths: list = field(default_factory=list)  # (path, count)
    suspect: bool = False  # also flagged live by scrape_detection
    suspect_score: float = 0.0
    suspect_signals: list = field(default_factory=list)


@dataclass
class TrafficReport:
    timeframe_label: str
    total_requests: int
    total_ips: int
    days: list = field(default_factory=list)  # DayRow
    status_counts: dict = field(default_factory=dict)  # "2xx" -> n
    top_paths: list = field(default_factory=list)  # (path, count)
    bucket_counts: dict = field(default_factory=dict)  # bucket -> n IPs
    ips: list = field(default_factory=list)  # IpProfile, worst first


@dataclass
class EventTrafficRow:
    event_id: int
    name: str  # "(deleted event #n)" when the event is gone
    date_label: str
    hits: int
    visitors: int
    sparkline: str
    # detail fields, empty when the event was deleted
    venue: str = ""
    status: str = ""
    genre: str = ""
    submitter: str = ""
    # (iso date, hits, visitors) for days with recorded views, newest first
    recent_days: list = field(default_factory=list)


@dataclass
class EventsResult:
    timeframe_label: str
    spark_days: int
    rows: list = field(default_factory=list)


@dataclass
class ErrorPathRow:
    path: str
    count: int
    statuses: dict = field(default_factory=dict)  # "502" -> n
    unique_ips: int = 0
    last_seen: str = ""


@dataclass
class ErrorsReport:
    timeframe_label: str
    total_requests: int
    total_5xx: int
    statuses: dict = field(default_factory=dict)  # "502" -> n, all paths
    rows: list = field(default_factory=list)  # ErrorPathRow, most 5xx first


def _log_files():
    files = find_log_files()
    if not files and ACCESS_LOG.is_file():
        files = [ACCESS_LOG]  # gunicorn dev log; find_log_files never sees it
    if not files:
        raise AdminError(
            f"No access logs found (checked nginx locations and {ACCESS_LOG})."
        )
    return files


def _parse_line(line):
    match = _NGINX_RE.match(line) or _GUNICORN_RE.match(line)
    if match is None:
        return None
    try:
        ts = datetime.strptime(match["ts"][:20], "%d/%b/%Y:%H:%M:%S")
    except ValueError:
        return None
    path = match["path"].split("?", 1)[0]
    return match["ip"], ts, path, match["status"], match["ref"], match["ua"].strip()


class _IpStats:
    __slots__ = (
        "times",
        "uas",
        "paths",
        "statuses",
        "event_ids",
        "hit_home",
        "probe",
        "honeypot",
        "internal_ref",
    )

    def __init__(self):
        self.times = []
        self.uas = set()
        self.paths = Counter()
        self.statuses = Counter()
        self.event_ids = []  # arrival order, for enumeration detection
        self.hit_home = False
        self.probe = False
        self.honeypot = False
        self.internal_ref = False


def _max_per_minute(times):
    """Largest request count inside any sliding 60 s window (times sorted)."""
    best = 0
    lo = 0
    for hi, t in enumerate(times):
        while t - times[lo] > timedelta(seconds=60):
            lo += 1
        best = max(best, hi - lo + 1)
    return best


def _longest_ascending_run(ids):
    best = run = 1 if ids else 0
    for prev, cur in zip(ids, ids[1:]):
        run = run + 1 if cur > prev else 1
        best = max(best, run)
    return best


def _score_ip(s):
    """(score, ["signal (+w)", ...]) from one IP's accumulated behaviour."""
    signals = []

    def fire(name, weight):
        signals.append(f"{name} (+{weight:g})")
        return weight

    score = 0.0
    n = len(s.times)
    if s.honeypot:
        score += fire("honeypot", 5.0)
    if s.probe:
        score += fire("probe-paths", 3.5)
    if any(_SCRIPTED_RE.search(ua) for ua in s.uas):
        score += fire("scripted-ua", 3.0)
    if len(s.uas) > 5:
        score += fire("ua-rotation", 2.5)
    if any(not ua or ua == "-" for ua in s.uas):
        score += fire("missing-ua", 2.0)
    per_min = _max_per_minute(s.times)
    if per_min >= 120:
        score += fire("burst", 3.5)
    elif per_min >= 60:
        score += fire("burst", 2.0)
    if n >= 500:
        score += fire("high-volume", 2.0)
    if n >= 20:
        gaps = [(b - a).total_seconds() for a, b in zip(s.times, s.times[1:])]
        mean = statistics.fmean(gaps)
        # 2 s floor: log timestamps have 1 s resolution, so sub-2 s means
        # collapse into indistinguishable bursts already caught above.
        if 2 <= mean <= 15 and statistics.pstdev(gaps) / mean < 0.3:
            score += fire("metronomic", 2.0)
    if _longest_ascending_run(s.event_ids) >= 8:
        score += fire("enumeration", 2.0)
    if not s.hit_home and not s.internal_ref and n >= 5:
        score += fire("no-navigation", 1.5)
    ok = sum(v for k, v in s.statuses.items() if k[0] in "23")
    if n and ok == 0:
        score += fire("all-errors", 1.5)
    return score, signals


def _bucket(s, score):
    """Classify one IP; mirrors traffic_audit.sh's real-user heuristic."""
    if any(_CRAWLER_RE.search(ua) for ua in s.uas):
        return "crawler"
    if score >= BOT_THRESHOLD:
        return "bot"
    if score >= SUSPICIOUS_THRESHOLD:
        return "suspicious"
    n = len(s.times)
    browser = any(
        ua.lower().startswith("mozilla") and not _SCRIPTED_RE.search(ua) for ua in s.uas
    )
    ok = sum(v for k, v in s.statuses.items() if k[0] in "23")
    if browser and len(s.uas) <= 5 and not s.probe and ok and 2 <= n < 500:
        return "human"
    return "unclear"


def analyze(timeframe="7d", limit=50):
    """Scan the access logs once and score every client IP.

    Needs an app context (for the ScrapeSuspect cross-reference). `limit`
    caps the per-IP list; day trend and totals always cover everything.
    """
    if timeframe not in TIMEFRAMES:
        raise AdminError(f"Unknown timeframe {timeframe!r}.")
    files = _log_files()
    valid_dates = _build_valid_dates(*_date_range(timeframe))

    per_ip = {}
    day_requests = Counter()
    day_ips = {}
    status_counts = Counter()
    path_counts = Counter()

    for line in _iter_filtered_lines(files, valid_dates):
        parsed = _parse_line(line)
        if parsed is None:
            continue
        ip, ts, path, status, ref, ua = parsed
        if path == "/admin" or path.startswith("/admin/"):
            continue  # _is_admin_request only understands the quoted format
        s = per_ip.setdefault(ip, _IpStats())
        s.times.append(ts)
        s.uas.add(ua)
        s.paths[path] += 1
        s.statuses[status] += 1
        match = _EVENT_PAGE_RE.match(path)
        if match:
            s.event_ids.append(int(match.group(1)))
        if path == "/":
            s.hit_home = True
        if ref not in ("", "-") and "://" in ref:
            s.internal_ref = s.internal_ref or "/" in ref.split("://", 1)[1]
        if path.startswith(_HONEYPOT_PREFIX):
            s.honeypot = True
        if _PROBE_RE.search(path):
            s.probe = True
        day = ts.date()
        day_requests[day] += 1
        day_ips.setdefault(day, set()).add(ip)
        status_counts[f"{status[0]}xx"] += 1
        path_counts[path] += 1

    if not per_ip:
        raise AdminError(
            f"No log entries found for {TIMEFRAMES[timeframe]} "
            f"({len(files)} log file(s) checked)."
        )

    require_schema(db, (ScrapeSuspect,), _SCHEMA_HINT)
    suspects = {row.ip: row for row in ScrapeSuspect.query.all()}

    profiles = []
    human_days = Counter()  # date -> humans active that day
    bucket_counts = Counter()
    for ip, s in per_ip.items():
        s.times.sort()
        score, signals = _score_ip(s)
        bucket = _bucket(s, score)
        bucket_counts[bucket] += 1
        if bucket == "human":
            for day in {t.date() for t in s.times}:
                human_days[day] += 1
        suspect = suspects.get(ip)
        ua_sample = next((ua for ua in sorted(s.uas) if ua and ua != "-"), "-")
        profiles.append(
            IpProfile(
                ip=ip,
                bucket=bucket,
                score=round(score, 1),
                signals=signals,
                requests=len(s.times),
                first_seen=s.times[0].isoformat(sep=" ", timespec="seconds"),
                last_seen=s.times[-1].isoformat(sep=" ", timespec="seconds"),
                ua_sample=ua_sample,
                n_uas=len(s.uas),
                top_paths=s.paths.most_common(5),
                suspect=suspect is not None,
                suspect_score=suspect.score if suspect else 0.0,
                suspect_signals=json.loads(suspect.signals)
                if suspect and suspect.signals
                else [],
            )
        )
    # worst first; humans and unclear sink to the bottom by score, then size
    profiles.sort(key=lambda p: (-p.score, -p.requests, p.ip))

    days = [
        DayRow(
            date=day.isoformat(),
            requests=day_requests[day],
            unique_ips=len(day_ips[day]),
            est_humans=human_days.get(day, 0),
        )
        for day in sorted(day_requests)
    ]
    return TrafficReport(
        timeframe_label=TIMEFRAMES[timeframe],
        total_requests=sum(day_requests.values()),
        total_ips=len(per_ip),
        days=days,
        status_counts=dict(sorted(status_counts.items())),
        top_paths=path_counts.most_common(10),
        bucket_counts=dict(bucket_counts),
        ips=profiles[:limit],
    )


class _PathErrors:
    __slots__ = ("statuses", "ips", "last_seen")

    def __init__(self):
        self.statuses = Counter()
        self.ips = set()
        self.last_seen = None


def server_errors(timeframe="7d", limit=50):
    """Paths ranked by 5xx responses in the window.

    Same log scan as analyze(), but no per-IP scoring — just which paths the
    app is failing on and with which status codes. Empty `rows` means the
    window had traffic but no 5xx at all, which is the good outcome, not an
    error. Needs no app context.
    """
    if timeframe not in TIMEFRAMES:
        raise AdminError(f"Unknown timeframe {timeframe!r}.")
    files = _log_files()
    valid_dates = _build_valid_dates(*_date_range(timeframe))

    total = 0
    status_totals = Counter()
    per_path = {}
    for line in _iter_filtered_lines(files, valid_dates):
        parsed = _parse_line(line)
        if parsed is None:
            continue
        ip, ts, path, status, _ref, _ua = parsed
        if path == "/admin" or path.startswith("/admin/"):
            continue
        total += 1
        if status[0] != "5":
            continue
        status_totals[status] += 1
        s = per_path.setdefault(path, _PathErrors())
        s.statuses[status] += 1
        s.ips.add(ip)
        if s.last_seen is None or ts > s.last_seen:
            s.last_seen = ts

    if total == 0:
        raise AdminError(
            f"No log entries found for {TIMEFRAMES[timeframe]} "
            f"({len(files)} log file(s) checked)."
        )

    rows = [
        ErrorPathRow(
            path=path,
            count=sum(s.statuses.values()),
            statuses=dict(sorted(s.statuses.items())),
            unique_ips=len(s.ips),
            last_seen=s.last_seen.isoformat(sep=" ", timespec="seconds"),
        )
        for path, s in per_path.items()
    ]
    rows.sort(key=lambda r: (-r.count, r.path))
    return ErrorsReport(
        timeframe_label=TIMEFRAMES[timeframe],
        total_requests=total,
        total_5xx=sum(status_totals.values()),
        statuses=dict(sorted(status_totals.items())),
        rows=rows[:limit],
    )


def _spark(values):
    peak = max(values) if values else 0
    if peak == 0:
        return " " * len(values)
    top = len(_SPARK_CHARS) - 1
    return "".join(
        _SPARK_CHARS[(value * (top - 1)) // peak + 1 if value else 0]
        for value in values
    )


def events(timeframe="30d", limit=25):
    """Events ranked by recorded views in the window, with a per-day trend.

    Reads EventDailyViews (durable beyond log rotation, already bot-filtered
    by services/event_views.py) — no log parsing here. Needs an app context.
    """
    if timeframe not in TIMEFRAMES:
        raise AdminError(f"Unknown timeframe {timeframe!r}.")
    require_schema(db, (EventDailyViews,), _SCHEMA_HINT)

    start, end = _date_range(timeframe)
    q = db.session.query(
        EventDailyViews.event_id,
        func.sum(EventDailyViews.hits).label("hits"),
        func.sum(EventDailyViews.visitors).label("visitors"),
    )
    if start is not None:
        q = q.filter(EventDailyViews.date >= start)
    ranked = (
        q.group_by(EventDailyViews.event_id)
        .order_by(func.sum(EventDailyViews.hits).desc())
        .limit(limit)
        .all()
    )
    if not ranked:
        raise AdminError(
            "No recorded event views in this window (EventDailyViews is "
            "filled by the analytics scheduler in production)."
        )

    ids = [row.event_id for row in ranked]
    events_by_id = {event.id: event for event in Event.query.filter(Event.id.in_(ids))}
    today = datetime.now().date()
    spark_start = today - timedelta(days=_SPARK_DAYS - 1)
    if start is not None:
        spark_start = max(spark_start, start)
    n_days = (today - spark_start).days + 1
    daily = {}  # (event_id, date) -> (hits, visitors), inside the spark window
    for row in EventDailyViews.query.filter(
        EventDailyViews.event_id.in_(ids), EventDailyViews.date >= spark_start
    ):
        daily[(row.event_id, row.date)] = (row.hits, row.visitors)

    rows = []
    for row in ranked:
        event = events_by_id.get(row.event_id)
        series = [
            daily.get((row.event_id, spark_start + timedelta(days=i)), (0, 0))[0]
            for i in range(n_days)
        ]
        recent = [
            (day.isoformat(), hits, visitors)
            for (event_id, day), (hits, visitors) in sorted(daily.items(), reverse=True)
            if event_id == row.event_id
        ]
        venue = ""
        if event and event.venue:
            venue = event.venue.name
            if event.venue.city:
                venue += f", {event.venue.city}"
        rows.append(
            EventTrafficRow(
                event_id=row.event_id,
                name=event.name if event else f"(deleted event #{row.event_id})",
                date_label=event.date.strftime("%Y-%m-%d") if event else "",
                hits=row.hits,
                visitors=row.visitors,
                sparkline=_spark(series),
                venue=venue,
                status=event.status if event else "",
                genre=event.genre or "" if event else "",
                submitter=event.submitter.email if event and event.submitter else "",
                recent_days=recent,
            )
        )
    return EventsResult(
        timeframe_label=TIMEFRAMES[timeframe], spark_days=n_days, rows=rows
    )
