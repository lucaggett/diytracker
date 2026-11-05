"""Nginx access log helpers: the GoAccess report and the Traffic panel's cache.

The numbers themselves are counted in services/human_traffic.py.
"""

import contextlib
import gzip
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time as time_module
from datetime import datetime, timedelta
from pathlib import Path

from diytracker.paths import ROOT

BASE_DIR = ROOT
REPORT_PATH = BASE_DIR / "instance" / "nginx_report.html"
STATS_PATH = BASE_DIR / "instance" / "analytics_stats.json"
STATS_WINDOW_DAYS = 30
STATS_INTERVAL_MINUTES = 15

TIMEFRAMES = [
    ("today", "Today"),
    ("yesterday", "Yesterday"),
    ("7d", "Last 7 days"),
    ("30d", "Last 30 days"),
    ("3mo", "Last 3 months"),
    ("all", "All time"),
]


def find_log_files() -> list[Path]:
    dev_log = BASE_DIR / "logs" / "nginx_access.log"
    if dev_log.exists():
        return [dev_log]

    prod_dir = Path("/var/log/nginx")
    if prod_dir.exists():
        pattern = os.environ.get("NGINX_LOG_PATTERN", "access.log")
        files = sorted(prod_dir.glob(f"{pattern}*"))
        return [f for f in files if f.is_file()]

    return []


def _date_range(timeframe: str):
    today = datetime.now().date()
    if timeframe == "today":
        return today, today
    if timeframe == "yesterday":
        d = today - timedelta(days=1)
        return d, d
    if timeframe == "7d":
        return today - timedelta(days=6), today
    if timeframe == "30d":
        return today - timedelta(days=29), today
    if timeframe == "3mo":
        return today - timedelta(days=89), today
    return None, None  # all


def _build_valid_dates(start, end) -> set[str] | None:
    if start is None:
        return None
    dates = set()
    d = start
    while d <= end:
        dates.add(d.strftime("%d/%b/%Y"))
        d += timedelta(days=1)
    return dates


def _extract_log_date(line: str) -> str | None:
    try:
        idx = line.index("[")
        return line[idx + 1 : idx + 12]  # DD/Mon/YYYY
    except ValueError:
        return None


def _is_admin_request(line: str) -> bool:
    # Admin dashboard traffic (including its 60s stats polling) must not count
    # as visitors, or the analytics end up measuring their own dashboard.
    try:
        start = line.index('"') + 1
        request = line[start : line.index('"', start)]
    except ValueError:
        return False
    parts = request.split(" ")
    if len(parts) < 2:
        return False
    path = parts[1].split("?", 1)[0]
    return path == "/admin" or path.startswith("/admin/")


def _iter_log_lines(files: list[Path], valid_dates: set[str] | None):
    """Log lines inside the date window, admin requests included."""
    for path in files:
        try:
            opener = (
                gzip.open(path, "rt", errors="replace")  # noqa: SIM115 - closed by the `with` on the next line
                if path.suffix == ".gz"
                else path.open(errors="replace")
            )
            with opener as f:
                for line in f:
                    if valid_dates is None or _extract_log_date(line) in valid_dates:
                        yield line
        except OSError:  # noqa: PERF203 - one unreadable log mustn't stop the rest
            continue


def _iter_filtered_lines(files: list[Path], valid_dates: set[str] | None):
    for line in _iter_log_lines(files, valid_dates):
        if not _is_admin_request(line):
            yield line


# goaccess over a full production log directory is minutes, not seconds, and
# generate_report() is reachable from a request on a sync worker. Bound it so
# a pathological run releases the worker instead of hanging until nginx's
# proxy_read_timeout gives up on it.
GOACCESS_TIMEOUT_SECONDS = 300


def _goaccess_argv(output_path) -> list[str]:
    return [
        "goaccess",
        "-",
        "--log-format=COMBINED",
        "--date-format=%d/%b/%Y",
        "--time-format=%H:%M:%S",
        f"--output={output_path}",
        "--ignore-crawlers",
        "--no-progress",
    ]


def _run_goaccess(lines, output_path, empty_message: str) -> tuple[bool, str]:
    """Stream *lines* into goaccess, writing its report to *output_path*.

    Streams rather than materialising the filtered log set first: "all time"
    over a production log directory is hundreds of megabytes, and
    `"".join(...)` held all of it in memory twice — the str and the encoded
    copy subprocess makes of it — inside a request-serving worker.

    stderr goes to a temp file, not a pipe: writing stdin while goaccess wrote
    to a full stderr pipe would deadlock both sides.
    """
    stream = iter(lines)
    try:
        first = next(stream)
    except StopIteration:
        return False, empty_message

    env = {**os.environ, "LANG": "C", "LC_ALL": "C"}
    with tempfile.TemporaryFile(mode="w+", errors="replace") as errfile:
        proc = subprocess.Popen(  # noqa: S603 - fixed argv, goaccess resolved from PATH
            _goaccess_argv(output_path),
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=errfile,
            text=True,
            env=env,
        )
        try:
            proc.stdin.write(first)
            for line in stream:
                proc.stdin.write(line)
        except BrokenPipeError:
            # goaccess exited early; its returncode below is the real answer.
            pass
        finally:
            with contextlib.suppress(BrokenPipeError, OSError):
                proc.stdin.close()
        try:
            proc.wait(timeout=GOACCESS_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            return False, f"goaccess timed out after {GOACCESS_TIMEOUT_SECONDS}s"
        if proc.returncode != 0:
            errfile.seek(0)
            return False, f"goaccess error: {errfile.read().strip()}"
    return True, ""


def generate_report(timeframe: str = "7d") -> tuple[bool, str]:
    """Run goaccess and write an HTML report. Returns (success, message)."""
    if not shutil.which("goaccess"):
        return False, "goaccess not found in PATH"

    log_files = find_log_files()
    if not log_files:
        return False, "No nginx access log files found"

    start, end = _date_range(timeframe)
    valid_dates = _build_valid_dates(start, end)

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    ok, message = _run_goaccess(
        _iter_filtered_lines(log_files, valid_dates),
        REPORT_PATH,
        f'No log entries found for timeframe "{timeframe}"',
    )
    if not ok:
        return False, message

    label = dict(TIMEFRAMES).get(timeframe, timeframe)
    return True, f"Report generated ({label}) from {len(log_files)} log file(s)"


def summarize_daily(day_map: dict, today) -> dict:
    """Build the Traffic panel's stats from {date: (hits, visitors)}.

    Multi-day "visitors" numbers are sums of daily uniques: a visitor active on
    3 days counts 3x. Good enough for trends.
    """

    def window_totals(start, end):
        hits = visitors = 0
        d = start
        while d <= end:
            h, v = day_map.get(d, (0, 0))
            hits += h
            visitors += v
            d += timedelta(days=1)
        return {"hits": hits, "visitors": visitors}

    yesterday = today - timedelta(days=1)
    win = STATS_WINDOW_DAYS
    cur_30d = window_totals(today - timedelta(days=win - 1), today)
    prev_30d = window_totals(
        today - timedelta(days=2 * win - 1), today - timedelta(days=win)
    )

    def growth_pct(cur, prev):
        return round((cur - prev) / prev * 100, 1) if prev else None

    daily = []
    d = today - timedelta(days=win - 1)
    while d <= today:
        h, v = day_map.get(d, (0, 0))
        daily.append({"date": d.isoformat(), "hits": h, "visitors": v})
        d += timedelta(days=1)

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "window_days": win,
        "totals": {
            "today": window_totals(today, today),
            "yesterday": window_totals(yesterday, yesterday),
            "last_7d": window_totals(today - timedelta(days=6), today),
            "last_30d": cur_30d,
        },
        "growth_30d": {
            "hits_pct": growth_pct(cur_30d["hits"], prev_30d["hits"]),
            "visitors_pct": growth_pct(cur_30d["visitors"], prev_30d["visitors"]),
            "prev_30d": prev_30d,
        },
        "daily": daily,
    }


def write_stats(day_map: dict, today) -> None:
    STATS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = STATS_PATH.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(summarize_daily(day_map, today)))
    tmp_path.replace(STATS_PATH)


def read_stats() -> dict | None:
    try:
        return json.loads(STATS_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def stats_age_seconds() -> float | None:
    try:
        return time_module.time() - STATS_PATH.stat().st_mtime
    except OSError:
        return None


_stats_lock = threading.Lock()
_stats_generating = False


def _generate_stats_guarded(app):
    global _stats_generating
    with _stats_lock:
        if _stats_generating:
            return
        _stats_generating = True
    try:
        # Lazy import: human_traffic reaches the models through page_views,
        # which this module must not do at import time.
        from diytracker.services.human_traffic import generate_traffic_stats

        with app.app_context():
            success, message = generate_traffic_stats()
        if success:
            app.logger.info(message)
        else:
            app.logger.warning("Analytics stats generation failed: %s", message)
    except Exception:
        app.logger.exception("Analytics stats generation failed")
    try:
        # Also lazy: scripts import this module without an app.
        from diytracker.services.event_views import generate_event_view_stats

        with app.app_context():
            success, message = generate_event_view_stats()
        if success:
            app.logger.info(message)
        else:
            app.logger.warning("Event view stats generation failed: %s", message)
    except Exception:
        app.logger.exception("Event view stats generation failed")
    try:
        from diytracker.services.page_views import generate_page_view_stats

        with app.app_context():
            success, message = generate_page_view_stats()
        if success:
            app.logger.info(message)
        else:
            app.logger.warning("Page view stats generation failed: %s", message)
    except Exception:
        app.logger.exception("Page view stats generation failed")
    finally:
        with _stats_lock:
            _stats_generating = False


def _stats_scheduler(app):
    while True:
        _generate_stats_guarded(app)
        time_module.sleep(STATS_INTERVAL_MINUTES * 60)


def start_stats_scheduler(app):
    thread = threading.Thread(
        target=_stats_scheduler, args=(app,), daemon=True, name="analytics-stats"
    )
    thread.start()
    return thread


def kick_stats_generation(app):
    """Fire a one-shot background regeneration (lazy fallback when no
    scheduler is running, e.g. dev mode or a dead first worker).
    """
    threading.Thread(
        target=_generate_stats_guarded,
        args=(app,),
        daemon=True,
        name="analytics-stats-kick",
    ).start()
