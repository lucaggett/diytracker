"""Preview or send the suspicious-traffic report by hand.

The background-jobs worker sends it on its own schedule (see
services/suspect_report.py); this is for checking what it would say, or
sending one now without waiting.

    uv run python scripts/suspect_report.py              # print, send nothing
    uv run python scripts/suspect_report.py --days 30    # longer window
    uv run python scripts/suspect_report.py --send       # mail it now

--send does not touch the scheduler's last-sent time, so the regular report
still goes out on its usual day.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from diytracker.app import create_app
from diytracker.services.mail import send_mail
from diytracker.services.suspect_report import build_report


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--days", type=int, help="window (default: the config interval)"
    )
    parser.add_argument("--send", action="store_true", help="mail the report now")
    args = parser.parse_args()

    app = create_app()
    days = args.days or app.config["SUSPECT_REPORT_INTERVAL_DAYS"] or 7
    with app.app_context():
        subject, body = build_report(days)

    if not args.send:
        print(f"Subject: {subject}\n\n{body}")
        return
    to_address = app.config["SUSPECT_REPORT_TO"]
    send_mail(to_address, subject, body)
    print(f"Sent to {to_address}: {subject}")


if __name__ == "__main__":
    main()
