"""Traffic analysis: behavioural bot inference, offenders, origin networks,
device mix and event view distribution.

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
from diytracker.admin.rdns import resolve_many
from diytracker.models import Event, EventDailyViews, ScrapeSuspect, db
from diytracker.services.analytics import (
    _build_valid_dates,
    _date_range,
    _iter_filtered_lines,
    find_log_files,
)

# Capped at 90 days on purpose: an "all logs" window grew with every rotated
# file on disk and mixed year-old traffic into a picture of what the site is
# doing now. "3mo" is analytics._date_range()'s own key for today-89 days, so
# the log-line prefilter keeps doing the date work.
TIMEFRAMES = {
    "7d": "last 7 days",
    "30d": "last 30 days",
    "3mo": "last 90 days",
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

# Buckets worth chasing in the scrapers view. Crawlers are included: they are
# self-declared rather than stealthy, but they are still the traffic.
_OFFENDER_BUCKETS = ("crawler", "bot", "suspicious")
REPEAT_DAYS = 3  # distinct active days before an offender counts as recurring

# Suffixes where the registrable name is three labels, not two, so
# "foo.example.co.uk" doesn't collapse to "co.uk".
_MULTI_SUFFIXES = frozenset(
    {
        "co.uk",
        "org.uk",
        "ac.uk",
        "gov.uk",
        "co.jp",
        "com.au",
        "net.au",
        "com.br",
        "co.nz",
        "co.za",
        "com.cn",
        "com.tr",
        "com.mx",
    }
)

# Device inference from the UA string. Regex, like every other signal in this
# module — a UA-parsing dependency would be a lot of machinery for three
# buckets, and the strings that matter here are stable.
_TABLET_RE = re.compile(r"iPad|Tablet|PlayBook|Silk", re.IGNORECASE)
_MOBILE_RE = re.compile(r"Mobi|iPhone|iPod|Android|Windows Phone|IEMobile")
_OS_PATTERNS = (
    ("Android", re.compile(r"Android")),
    ("iOS", re.compile(r"iPhone|iPad|iPod|CPU OS \d")),
    ("Windows", re.compile(r"Windows NT|Windows Phone")),
    ("ChromeOS", re.compile(r"CrOS")),
    ("macOS", re.compile(r"Macintosh|Mac OS X")),
    ("Linux", re.compile(r"Linux|X11|Ubuntu")),
)
# Order matters: every Chromium UA also says Safari, Edge says Chrome, and
# Samsung Internet says both — so the most specific claim wins.
_BROWSER_PATTERNS = (
    ("Edge", re.compile(r"Edg[A-Z]?/")),
    ("Samsung Internet", re.compile(r"SamsungBrowser")),
    ("Opera", re.compile(r"OPR/|Opera")),
    ("Vivaldi", re.compile(r"Vivaldi")),
    ("Firefox", re.compile(r"Firefox/|FxiOS")),
    ("Chrome", re.compile(r"Chrome/|CriOS|Chromium")),
    ("Safari", re.compile(r"Safari/")),
)

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
class ScraperRow:
    ip: str
    bucket: str  # crawler | bot | suspicious
    score: float
    requests: int
    share: float  # of all requests in the window, 0..1
    peak_per_min: int
    active_days: int
    first_seen: str
    last_seen: str
    honeypot: bool = False
    probe: bool = False
    live_flagged: bool = False  # also flagged by scrape_detection
    signals: list = field(default_factory=list)
    top_paths: list = field(default_factory=list)  # (path, count)
    host: str = ""  # rDNS name, "" when unresolved
    net24: str = ""
    ua_sample: str = ""


@dataclass
class ScrapersReport:
    timeframe_label: str
    total_requests: int
    offender_requests: int  # from crawler/bot/suspicious IPs
    human_requests: int
    repeat_offenders: int  # offenders seen on >= REPEAT_DAYS distinct days
    undetected_live: int  # live-flagged here but not scored as a bot
    unresolved_hosts: int  # rows still waiting for a PTR lookup
    rows: list = field(default_factory=list)  # ScraperRow, worst first


@dataclass
class HostRow:
    domain: str  # registrable rDNS domain, or the /24 when there is no name
    named: bool  # False when `domain` is the network fallback
    n_ips: int
    requests: int
    share: float  # of all requests in the window, 0..1
    buckets: dict = field(default_factory=dict)  # bucket -> n IPs
    offender_requests: int = 0  # from crawler/bot/suspicious IPs
    human_requests: int = 0
    n_net24: int = 0
    top_net24: str = ""
    sample_ips: list = field(default_factory=list)  # (ip, bucket, requests)


@dataclass
class HostsReport:
    timeframe_label: str
    total_requests: int
    total_ips: int
    resolved_ips: int
    pending_ips: int  # over the lookup budget, resolved on a later refresh
    offenders: list = field(default_factory=list)  # HostRow, most bot traffic
    humans: list = field(default_factory=list)  # HostRow, most human traffic


@dataclass
class DeviceRow:
    label: str
    n_ips: int
    requests: int
    share: float  # of the breakdown's IPs, 0..1


@dataclass
class DevicesReport:
    timeframe_label: str
    n_ips: int  # human-bucket IPs the breakdown is built from
    n_browser_unclear: int  # browser-UA IPs that scored as unclear
    requests: int
    form_factors: list = field(default_factory=list)  # DeviceRow
    systems: list = field(default_factory=list)
    browsers: list = field(default_factory=list)


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


class _Scan:
    """One pass over the access logs: per-IP behaviour plus daily totals.

    Shared by every view that needs raw log data, so cycling views in the TUI
    costs one scan each rather than one scan per statistic.
    """

    __slots__ = (
        "timeframe",
        "per_ip",
        "day_requests",
        "day_ips",
        "status_counts",
        "path_counts",
    )

    def __init__(self, timeframe):
        self.timeframe = timeframe
        self.per_ip = {}
        self.day_requests = Counter()
        self.day_ips = {}
        self.status_counts = Counter()
        self.path_counts = Counter()

    @property
    def total_requests(self):
        return sum(self.day_requests.values())


def _scan(timeframe):
    """Parse the logs for *timeframe*; raises AdminError on an empty window."""
    if timeframe not in TIMEFRAMES:
        raise AdminError(f"Unknown timeframe {timeframe!r}.")
    files = _log_files()
    valid_dates = _build_valid_dates(*_date_range(timeframe))
    scan = _Scan(timeframe)

    for line in _iter_filtered_lines(files, valid_dates):
        parsed = _parse_line(line)
        if parsed is None:
            continue
        ip, ts, path, status, ref, ua = parsed
        if path == "/admin" or path.startswith("/admin/"):
            continue  # _is_admin_request only understands the quoted format
        s = scan.per_ip.setdefault(ip, _IpStats())
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
        scan.day_requests[day] += 1
        scan.day_ips.setdefault(day, set()).add(ip)
        scan.status_counts[f"{status[0]}xx"] += 1
        scan.path_counts[path] += 1

    if not scan.per_ip:
        raise AdminError(
            f"No log entries found for {TIMEFRAMES[timeframe]} "
            f"({len(files)} log file(s) checked)."
        )

    for s in scan.per_ip.values():
        s.times.sort()
    return scan


def _classify(scan):
    """{ip: (bucket, score, signals)} for every IP in the scan."""
    verdicts = {}
    for ip, s in scan.per_ip.items():
        score, signals = _score_ip(s)
        verdicts[ip] = (_bucket(s, score), score, signals)
    return verdicts


def analyze(timeframe="7d", limit=50):
    """Scan the access logs once and score every client IP.

    Needs an app context (for the ScrapeSuspect cross-reference). `limit`
    caps the per-IP list; day trend and totals always cover everything.
    """
    scan = _scan(timeframe)
    per_ip = scan.per_ip
    verdicts = _classify(scan)

    require_schema(db, (ScrapeSuspect,), _SCHEMA_HINT)
    suspects = {row.ip: row for row in ScrapeSuspect.query.all()}

    profiles = []
    human_days = Counter()  # date -> humans active that day
    bucket_counts = Counter()
    for ip, s in per_ip.items():
        bucket, score, signals = verdicts[ip]
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
            requests=scan.day_requests[day],
            unique_ips=len(scan.day_ips[day]),
            est_humans=human_days.get(day, 0),
        )
        for day in sorted(scan.day_requests)
    ]
    return TrafficReport(
        timeframe_label=TIMEFRAMES[timeframe],
        total_requests=scan.total_requests,
        total_ips=len(per_ip),
        days=days,
        status_counts=dict(sorted(scan.status_counts.items())),
        top_paths=scan.path_counts.most_common(10),
        bucket_counts=dict(bucket_counts),
        ips=profiles[:limit],
    )


def _ua_sample(s):
    return next((ua for ua in sorted(s.uas) if ua and ua != "-"), "-")


def _net24(ip):
    """The IP's /24 (or /48 for v6) as a display string."""
    if ":" in ip:
        return ":".join(ip.split(":")[:3]) + "::/48"
    parts = ip.split(".")
    if len(parts) != 4:
        return ip
    return ".".join(parts[:3]) + ".0/24"


def _registrable(hostname):
    """The registrable domain of a PTR name, e.g. ec2-1-2.eu.amazonaws.com ->
    amazonaws.com. Suffix-list-free: two labels, three for the handful of
    two-level ccTLDs in _MULTI_SUFFIXES."""
    labels = hostname.strip(".").lower().split(".")
    if len(labels) <= 2:
        return ".".join(labels)
    if ".".join(labels[-2:]) in _MULTI_SUFFIXES:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def _device(ua):
    """(form factor, OS, browser) inferred from a UA string.

    Only meaningful for browser UAs; scripted clients land in ("other",
    "other", "other") and callers keep them out of the breakdown.
    """
    if not ua or ua == "-" or _SCRIPTED_RE.search(ua) or _CRAWLER_RE.search(ua):
        return "other", "other", "other"
    if _TABLET_RE.search(ua):
        form = "tablet"
    elif _MOBILE_RE.search(ua):
        form = "mobile"
    elif ua.lower().startswith("mozilla"):
        form = "desktop"
    else:
        form = "other"
    system = next((name for name, rx in _OS_PATTERNS if rx.search(ua)), "other")
    browser = next((name for name, rx in _BROWSER_PATTERNS if rx.search(ua)), "other")
    return form, system, browser


def _breakdown(counter_ips, counter_requests):
    """DeviceRows for one dimension, biggest first."""
    total = sum(counter_ips.values())
    return [
        DeviceRow(
            label=label,
            n_ips=n,
            requests=counter_requests[label],
            share=n / total if total else 0.0,
        )
        for label, n in counter_ips.most_common()
    ]


def scrapers(timeframe="7d", limit=50):
    """The offenders: every IP scored as a crawler, bot or suspicious.

    Same scan and scoring as analyze(), narrowed to the traffic worth acting
    on and enriched with what makes a decision possible — how much of the
    window's traffic each one is, how hard it hit at peak, whether it keeps
    coming back, and where it resolves to. Needs an app context.
    """
    scan = _scan(timeframe)
    verdicts = _classify(scan)
    require_schema(db, (ScrapeSuspect,), _SCHEMA_HINT)
    suspects = {row.ip for row in ScrapeSuspect.query.with_entities(ScrapeSuspect.ip)}

    offenders = [
        (ip, s, *verdicts[ip])
        for ip, s in scan.per_ip.items()
        if verdicts[ip][0] in _OFFENDER_BUCKETS
    ]
    offenders.sort(key=lambda item: (-item[3], -len(item[1].times), item[0]))

    total = scan.total_requests
    offender_requests = sum(len(s.times) for _ip, s, *_rest in offenders)
    human_requests = sum(
        len(s.times) for ip, s in scan.per_ip.items() if verdicts[ip][0] == "human"
    )
    # Disagreement worth looking at: the live detector flagged it, but the
    # behavioural score over the whole window doesn't even find it
    # suspicious.
    undetected_live = sum(
        1
        for ip in suspects
        if ip in verdicts and verdicts[ip][0] not in _OFFENDER_BUCKETS
    )

    shown = offenders[:limit]
    hosts_by_ip = resolve_many([ip for ip, *_rest in shown])

    rows = []
    repeat = 0
    for ip, s, bucket, score, signals in shown:
        active_days = len({t.date() for t in s.times})
        if active_days >= REPEAT_DAYS:
            repeat += 1
        rows.append(
            ScraperRow(
                ip=ip,
                bucket=bucket,
                score=round(score, 1),
                requests=len(s.times),
                share=len(s.times) / total if total else 0.0,
                peak_per_min=_max_per_minute(s.times),
                active_days=active_days,
                first_seen=s.times[0].isoformat(sep=" ", timespec="seconds"),
                last_seen=s.times[-1].isoformat(sep=" ", timespec="seconds"),
                honeypot=s.honeypot,
                probe=s.probe,
                live_flagged=ip in suspects,
                signals=signals,
                top_paths=s.paths.most_common(5),
                host=hosts_by_ip.get(ip) or "",
                net24=_net24(ip),
                ua_sample=_ua_sample(s),
            )
        )
    return ScrapersReport(
        timeframe_label=TIMEFRAMES[timeframe],
        total_requests=total,
        offender_requests=offender_requests,
        human_requests=human_requests,
        repeat_offenders=repeat,
        undetected_live=undetected_live,
        unresolved_hosts=sum(1 for row in rows if not row.host),
        rows=rows,
    )


class _HostStats:
    __slots__ = ("ips", "requests", "buckets", "offender_requests", "human_requests")

    def __init__(self):
        self.ips = []  # (ip, bucket, requests)
        self.requests = 0
        self.buckets = Counter()
        self.offender_requests = 0
        self.human_requests = 0


def hosts(timeframe="7d", limit=50):
    """Where the traffic comes from, grouped by rDNS domain and /24.

    Two groupings because either alone lies: a PTR name says who owns the
    address but is missing for plenty of hosts, and a /24 catches one scraper
    cycling through a subnet but says nothing about whose subnet it is. So a
    row is keyed by the registrable domain when there is a name and by the /24
    when there isn't, and `named` says which.

    Lookups run on a budget (see admin/rdns.py); addresses over it are counted
    in `pending_ips` and get their names on a later refresh. Needs an app
    context.
    """
    scan = _scan(timeframe)
    verdicts = _classify(scan)

    by_requests = sorted(
        scan.per_ip.items(), key=lambda item: -len(item[1].times)
    )  # spend the lookup budget on the busiest addresses
    resolved = resolve_many([ip for ip, _s in by_requests])

    groups = {}
    named = {}
    nets = {}
    pending = 0
    for ip, s in by_requests:
        hostname = resolved.get(ip)
        if hostname:
            domain = _registrable(hostname)
        else:
            # No name — group by network instead of dumping every unresolved
            # address into one bucket. On a site with thousands of visitors
            # the lookup budget takes many refreshes to catch up, and a single
            # "(pending)" row that big says nothing at all.
            domain = _net24(ip)
            if ip not in resolved:
                pending += 1
        named[domain] = bool(hostname)
        bucket = verdicts[ip][0]
        n = len(s.times)
        group = groups.setdefault(domain, _HostStats())
        group.ips.append((ip, bucket, n))
        group.requests += n
        group.buckets[bucket] += 1
        if bucket in _OFFENDER_BUCKETS:
            group.offender_requests += n
        elif bucket == "human":
            group.human_requests += n
        nets.setdefault(domain, Counter())[_net24(ip)] += n

    total = scan.total_requests

    def row(domain, group):
        net_counts = nets[domain]
        return HostRow(
            domain=domain,
            named=named[domain],
            n_ips=len(group.ips),
            requests=group.requests,
            share=group.requests / total if total else 0.0,
            buckets=dict(group.buckets),
            offender_requests=group.offender_requests,
            human_requests=group.human_requests,
            n_net24=len(net_counts),
            top_net24=net_counts.most_common(1)[0][0],
            sample_ips=sorted(group.ips, key=lambda item: -item[2])[:10],
        )

    rows = [row(domain, group) for domain, group in groups.items()]
    offenders = sorted(
        (r for r in rows if r.offender_requests),
        key=lambda r: (-r.offender_requests, -r.n_ips, r.domain),
    )
    humans = sorted(
        (r for r in rows if r.human_requests),
        key=lambda r: (-r.human_requests, -r.n_ips, r.domain),
    )
    return HostsReport(
        timeframe_label=TIMEFRAMES[timeframe],
        total_requests=total,
        total_ips=len(scan.per_ip),
        resolved_ips=sum(1 for ip in scan.per_ip if resolved.get(ip)),
        pending_ips=pending,
        offenders=offenders[:limit],
        humans=humans[:limit],
    )


def devices(timeframe="7d"):
    """What the human traffic browses with: form factor, OS, browser.

    The unit is an IP over the window, not a person: an office behind one NAT
    counts once, a phone changing cell counts twice. Read the shares, not the
    absolutes. IPs that look like a browser but scored as `unclear` are
    counted separately rather than folded in, so the number stays honest about
    what it is built from. Needs an app context (via the shared classifier).
    """
    scan = _scan(timeframe)
    verdicts = _classify(scan)

    form_ips, form_reqs = Counter(), Counter()
    os_ips, os_reqs = Counter(), Counter()
    browser_ips, browser_reqs = Counter(), Counter()
    n_ips = requests = 0
    browser_unclear = 0

    for ip, s in scan.per_ip.items():
        bucket = verdicts[ip][0]
        ua = _ua_sample(s)
        form, system, browser = _device(ua)
        if bucket != "human":
            if bucket == "unclear" and form != "other":
                browser_unclear += 1
            continue
        n = len(s.times)
        n_ips += 1
        requests += n
        form_ips[form] += 1
        form_reqs[form] += n
        os_ips[system] += 1
        os_reqs[system] += n
        browser_ips[browser] += 1
        browser_reqs[browser] += n

    if not n_ips:
        raise AdminError(
            f"No human-classified traffic in {TIMEFRAMES[timeframe]} — "
            "nothing to break down by device."
        )

    return DevicesReport(
        timeframe_label=TIMEFRAMES[timeframe],
        n_ips=n_ips,
        n_browser_unclear=browser_unclear,
        requests=requests,
        form_factors=_breakdown(form_ips, form_reqs),
        systems=_breakdown(os_ips, os_reqs),
        browsers=_breakdown(browser_ips, browser_reqs),
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
