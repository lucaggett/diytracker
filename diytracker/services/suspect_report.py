"""Regular email report of suspicious clients, sent to the site's inbox.

Two sources, because neither one sees everything:

  * ScrapeSuspect rows, written live by services/scrape_detection.py. These
    are IPs that crossed the flag threshold on their own behaviour.
  * The nginx access logs, for what the per-IP detector can't see: refused
    /set-language/ requests (services/abuse.py) and rate-limited (429)
    clients. The proxy-pool scraper of July–October 2026 spent one IP per page
    and never scored high enough to become a ScrapeSuspect; it only shows up as
    a volume of refusals spread across many networks.

Detection stays detection: the report blocks nothing. It is evidence for a
human, who acts on it via BLOCKED_IPS / BLOCKED_USER_AGENTS or
/admin/scrape-suspects.

Like the other mails it is plain German text and bypasses the Babel catalogs
(see services/notifications.py). Timestamps are UTC throughout: ScrapeSuspect
columns are audit columns (models.utcnow), and nginx logs in UTC.
"""

import json
import re
import threading
import time as time_module
from collections import Counter
from datetime import datetime, timedelta

from diytracker.models import ScrapeSuspect, utcnow
from diytracker.paths import INSTANCE_DIR
from diytracker.services.analytics import (
    _build_valid_dates,
    _iter_filtered_lines,
    find_log_files,
)
from diytracker.services.mail import (
    MailNotConfiguredError,
    build_logger,
    send_mail,
)

LAST_REPORT_FILE = INSTANCE_DIR / "last_suspect_report.txt"
# How often the scheduler checks whether a report is due. The interval itself
# is days; an hour of slack on a weekly mail doesn't matter.
CHECK_INTERVAL_SECONDS = 3600
MAX_SUSPECTS = 25
MAX_NETWORKS = 10
MAX_RATE_LIMITED = 10
ADMIN_SUSPECTS_URL = "https://diytracker.ch/admin/scrape-suspects"

# Combined log format: ip, timestamp, request path, status.
_LINE_RE = re.compile(
    r"^(?P<ip>\S+) \S+ \S+ \[[^\]]*\] "
    r'"\S+ (?P<path>\S+) [^"]*" (?P<status>\d{3}) '
)


def build_report_logger():
    return build_logger("suspect_report", "suspect_report.log")


def get_last_report_time():
    try:
        with LAST_REPORT_FILE.open() as f:
            return datetime.fromisoformat(f.read().strip())
    except (FileNotFoundError, ValueError):
        return None


def set_last_report_time(dt):
    LAST_REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LAST_REPORT_FILE.open("w") as f:
        f.write(dt.isoformat())


def _net16(ip):
    """The IP's /16 (or /32 for v6) — proxy pools spread across hosts, not
    networks, so this is the unit worth blocking or reporting upstream.
    """
    if ":" in ip:
        return ":".join(ip.split(":")[:2]) + "::/32"
    parts = ip.split(".")
    if len(parts) != 4:
        return ip
    return ".".join(parts[:2]) + ".0.0/16"


def scan_logs(files, start, end):
    """Refused language switches and rate-limited IPs in [start, end] (dates).

    Returns {"refused": n, "refused_ips": n, "refused_networks": Counter,
    "rate_limited": Counter of ip -> 429 count}.
    """
    refused = 0
    refused_ips = set()
    networks = Counter()
    rate_limited = Counter()
    for line in _iter_filtered_lines(files, _build_valid_dates(start, end)):
        match = _LINE_RE.match(line)
        if match is None:
            continue
        status = match["status"]
        if status == "403" and match["path"].startswith("/set-language/"):
            refused += 1
            ip = match["ip"]
            if ip not in refused_ips:
                refused_ips.add(ip)
                networks[_net16(ip)] += 1
        elif status == "429":
            rate_limited[match["ip"]] += 1
    return {
        "refused": refused,
        "refused_ips": len(refused_ips),
        "refused_networks": networks,
        "rate_limited": rate_limited,
    }


def _fmt_num(n):
    return f"{n:,}".replace(",", "'")  # Swiss thousands separator


def _ips(n):
    return f"{_fmt_num(n)} IP" if n == 1 else f"{_fmt_num(n)} IPs"


def build_report(days, now=None, log_files=None):
    """(subject, body) for the *days* days up to *now* (UTC). Needs an app
    context for the ScrapeSuspect query.
    """
    now = now or utcnow()
    since = now - timedelta(days=days)
    span = f"{since:%d.%m.%Y} – {now:%d.%m.%Y}"

    suspects = (
        ScrapeSuspect.query.filter(ScrapeSuspect.last_seen >= since)
        .order_by(ScrapeSuspect.score.desc(), ScrapeSuspect.request_count.desc())
        .all()
    )
    new = sum(1 for s in suspects if s.first_seen >= since)

    files = find_log_files() if log_files is None else log_files
    logs = scan_logs(files, since.date(), now.date()) if files else None

    lines = [f"Verdächtige Zugriffe auf diytracker.ch, {span} (UTC).", ""]

    lines.append(f"VOM SCRAPE-DETEKTOR MARKIERT: {_ips(len(suspects))} ({new} neu)")
    lines.append("")
    if not suspects:
        lines.append("  Keine.")
    for s in suspects[:MAX_SUSPECTS]:
        signals = ", ".join(json.loads(s.signals or "[]")) or "–"
        paths = ", ".join(json.loads(s.sample_paths or "[]")[:4]) or "–"
        tag = "  neu" if s.first_seen >= since else ""
        lines += [
            f"  {s.ip}  Score {s.score:g}  {_fmt_num(s.request_count)} Anfragen{tag}",
            f"    Signale: {signals}",
            f"    Zuletzt: {s.last_seen:%d.%m.%Y %H:%M}",
            f"    UA: {s.user_agent or '–'}",
            f"    Pfade: {paths}",
        ]
    if len(suspects) > MAX_SUSPECTS:
        lines.append(f"  … und {len(suspects) - MAX_SUSPECTS} weitere.")
    lines += ["", f"  Alle Einträge: {ADMIN_SUSPECTS_URL}", ""]

    if logs is None:
        lines.append("ZUGRIFFSLOGS: keine nginx-Logs gefunden, Abschnitt fehlt.")
    else:
        lines.append(
            "ABGEWIESENE SPRACHWECHSEL (/set-language/ ohne Klick von der Seite): "
            f"{_fmt_num(logs['refused'])} Anfragen von "
            f"{_ips(logs['refused_ips'])}"
        )
        if logs["refused_networks"]:
            lines.append("  Häufigste Netze (IPs pro /16):")
            lines += [
                f"    {net}  {_fmt_num(n)}"
                for net, n in logs["refused_networks"].most_common(MAX_NETWORKS)
            ]
        lines.append("")
        limited = logs["rate_limited"]
        lines.append(
            f"GEDROSSELT (429): {_fmt_num(sum(limited.values()))} Anfragen von "
            f"{_ips(len(limited))}"
        )
        lines += [
            f"    {ip}  {_fmt_num(n)}"
            for ip, n in limited.most_common(MAX_RATE_LIMITED)
        ]

    lines += [
        "",
        "--",
        (
            "Automatischer Bericht von diytracker. Gesperrt wird nichts: IPs und "
            "User-Agents lassen sich über BLOCKED_IPS / BLOCKED_USER_AGENTS in "
            "der .env sperren."
        ),
        "",
    ]
    subject = f"diytracker: verdächtige Zugriffe {span}"
    return subject, "\n".join(lines)


def send_report(app, now=None):
    """Build and send one report now. Returns True when the mail went out."""
    logger = build_report_logger()
    to_address = app.config["SUSPECT_REPORT_TO"]
    days = app.config["SUSPECT_REPORT_INTERVAL_DAYS"] or 7
    with app.app_context():
        subject, body = build_report(days, now=now)
    send_mail(to_address, subject, body)
    logger.info("suspect report sent to %s (%s)", to_address, subject)
    return True


def maybe_send_report(app, now=None):
    """Send the report if one is due. Never raises; returns True once the
    period is handled.

    "Due" is measured from the last report actually sent, persisted in
    instance/ so a restart doesn't reset the clock. A missing mail setup
    counts as handled for the period (it won't fix itself within the hour,
    and retrying would log a warning every hour); an SMTP failure leaves the
    report due, so the next check retries.
    """
    days = app.config["SUSPECT_REPORT_INTERVAL_DAYS"]
    if not days or not app.config["SUSPECT_REPORT_TO"]:
        return False
    now = now or utcnow()
    last = get_last_report_time()
    if last is not None and now - last < timedelta(days=days):
        return False
    logger = build_report_logger()
    try:
        send_report(app, now=now)
    except MailNotConfiguredError:
        logger.warning("suspect report skipped: mail is not configured")
    except Exception:
        logger.exception("suspect report failed; retrying at the next check")
        return False
    set_last_report_time(now)
    return True


def _report_scheduler(app):
    while True:
        try:
            maybe_send_report(app)
        except Exception:  # the thread must outlive any one run
            app.logger.exception("suspect report scheduler error")
        time_module.sleep(CHECK_INTERVAL_SECONDS)


def start_report_scheduler(app):
    thread = threading.Thread(
        target=_report_scheduler, args=(app,), daemon=True, name="suspect-report"
    )
    thread.start()
    return thread
