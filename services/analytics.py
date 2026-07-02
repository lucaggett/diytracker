"""GoAccess report generation from nginx access logs."""

import gzip
import os
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
REPORT_PATH = BASE_DIR / "instance" / "nginx_report.html"

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


def _iter_filtered_lines(files: list[Path], valid_dates: set[str] | None):
    for path in files:
        try:
            opener = (
                gzip.open(path, "rt", errors="replace")
                if path.suffix == ".gz"
                else open(path, errors="replace")
            )
            with opener as f:
                for line in f:
                    if valid_dates is None or _extract_log_date(line) in valid_dates:
                        yield line
        except OSError:
            continue


def generate_report(timeframe: str = "7d") -> tuple[bool, str]:
    """Run goaccess and write an HTML report. Returns (success, message)."""
    if not shutil.which("goaccess"):
        return False, "goaccess not found in PATH"

    log_files = find_log_files()
    if not log_files:
        return False, "No nginx access log files found"

    start, end = _date_range(timeframe)
    valid_dates = _build_valid_dates(start, end)

    content = "".join(_iter_filtered_lines(log_files, valid_dates))
    if not content.strip():
        return False, f'No log entries found for timeframe "{timeframe}"'

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)

    env = {**os.environ, "LANG": "C", "LC_ALL": "C"}
    result = subprocess.run(
        [
            "goaccess",
            "-",
            "--log-format=COMBINED",
            "--date-format=%d/%b/%Y",
            "--time-format=%H:%M:%S",
            f"--output={REPORT_PATH}",
            "--ignore-crawlers",
            "--no-progress",
        ],
        input=content,
        capture_output=True,
        text=True,
        env=env,
    )

    if result.returncode != 0:
        return False, f"goaccess error: {result.stderr.strip()}"

    label = dict(TIMEFRAMES).get(timeframe, timeframe)
    return True, f"Report generated ({label}) from {len(log_files)} log file(s)"
