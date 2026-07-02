"""Generate a GoAccess HTML report from nginx access logs.

Usage:
    python scripts/generate_goaccess_report.py [--timeframe TIMEFRAME]

Timeframes: today, yesterday, 7d, 30d, 3mo, all  (default: 7d)

Output: instance/nginx_report.html
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from diytracker.services.analytics import TIMEFRAMES, find_log_files, generate_report


def main():
    choices = [t for t, _ in TIMEFRAMES]
    parser = argparse.ArgumentParser(description="Generate GoAccess nginx report")
    parser.add_argument(
        "--timeframe",
        "-t",
        choices=choices,
        default="7d",
        help=f"Time range for the report (default: 7d). Choices: {', '.join(choices)}",
    )
    args = parser.parse_args()

    log_files = find_log_files()
    if log_files:
        print(f"Log files found: {', '.join(str(f) for f in log_files)}")
    else:
        print("Warning: no log files detected", file=sys.stderr)

    label = dict(TIMEFRAMES)[args.timeframe]
    print(f"Generating report for: {label}")

    success, message = generate_report(args.timeframe)
    if success:
        print(f"OK: {message}")
    else:
        print(f"Error: {message}", file=sys.stderr)
        sys.exit(1)

    from diytracker.services.analytics import REPORT_PATH

    print(f"Report saved to: {REPORT_PATH}")


if __name__ == "__main__":
    main()
