#!/usr/bin/env python3
"""diytracker management CLI.

Tail the server logs and manage users. Run it through uv so the project's
dependencies are available:

    uv run python manage.py <command> [options]

Commands:
    logs     Tail the access or error log
    user     Manage users (list / add / passwd / admin / invite / delete)
    venue    Manage venues (dedup)

The gunicorn server itself runs under systemd — see deploy/README.md.
"""

import argparse
import getpass
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
ACCESS_LOG = PROJECT_ROOT / "logs" / "access_log_diytracker"
ERROR_LOG = PROJECT_ROOT / "logs" / "error_log_diytracker"


# ── output helpers ────────────────────────────────────────────────────────────


def _use_color():
    return sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def _c(text, code):
    return f"\033[{code}m{text}\033[0m" if _use_color() else text


def green(t):
    return _c(t, "32")


# ── logs ──────────────────────────────────────────────────────────────────────


def cmd_logs(args):
    log = ERROR_LOG if args.error else ACCESS_LOG
    if not log.exists():
        print(f"No log file at {log}")
        return 1
    cmd = ["tail"]
    if args.follow:
        cmd.append("-f")
    cmd += ["-n", str(args.lines), str(log)]
    try:
        return subprocess.run(cmd).returncode
    except KeyboardInterrupt:
        return 0


# ── user management ───────────────────────────────────────────────────────────


def _load_app(db_path=None):
    """Build the Flask app via the factory (never starts the scraper thread)."""
    sys.path.insert(0, str(PROJECT_ROOT))
    from dotenv import load_dotenv

    from diytracker.app import create_app
    from diytracker.config import Config
    from diytracker.models import db, Submitter

    load_dotenv(PROJECT_ROOT / ".env")
    try:
        config = Config.from_env()
    except RuntimeError as exc:
        sys.exit(str(exc))
    if db_path is not None:
        config.SQLALCHEMY_DATABASE_URI = f"sqlite:///{db_path}"
    return create_app(config), db, Submitter


def _send_email(to_address, subject, body):
    import smtplib
    import ssl
    from email.message import EmailMessage

    message = EmailMessage()
    message.set_content(body)
    message["Subject"] = subject
    message["From"] = "info@diytracker.ch"
    message["To"] = to_address

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(os.environ["EMAIL_SERVER"], 465, context=context) as server:
        server.login(os.environ["EMAIL_USERNAME"], os.environ["EMAIL_PASSWORD"])
        server.send_message(message)
    print(f"  Invite email sent to {to_address}")


def _send_invite_email(email, token):
    _send_email(
        email,
        "Set your diytracker.ch password",
        f"You've been invited to diytracker.ch!\n\n"
        f"Set your password using this link (valid for 7 days):\n"
        f"https://diytracker.ch/set-password/{token}\n\n"
        f"If you have any questions, contact Luc at luc@aggett.com",
    )


def _find_user(Submitter, email):
    user = Submitter.query.filter_by(email=email).first()
    if not user:
        sys.exit(f"No user found with email {email!r}.")
    return user


def cmd_user_list(args):
    app, _db, Submitter = _load_app()
    with app.app_context():
        users = Submitter.query.order_by(Submitter.email).all()
        if not users:
            print("No users.")
            return 0
        print()
        print(f"  {'':>3}  {'ADMIN':<6}  {'PW':<6}  email")
        print(f"  {'-' * 50}")
        for i, user in enumerate(users, 1):
            admin_flag = "admin" if user.is_admin else ""
            pw_flag = "set" if user.password_hash else "NO PW"
            print(f"  {i:>3}  {admin_flag:<6}  {pw_flag:<6}  {user.email}")
        print()
    return 0


def cmd_user_add(args):
    app, db, Submitter = _load_app()
    with app.app_context():
        if Submitter.query.filter_by(email=args.email).first():
            sys.exit(f"A user with email {args.email!r} already exists.")
        user = Submitter(email=args.email)
        token = user.generate_invite_token()
        db.session.add(user)
        db.session.commit()
        print(f"{green('Created')} user {args.email}.")
        if not args.no_email:
            _send_invite_email(args.email, token)
        else:
            print(f"  Invite link: https://diytracker.ch/set-password/{token}")
    return 0


def cmd_user_passwd(args):
    app, db, Submitter = _load_app()
    with app.app_context():
        user = _find_user(Submitter, args.email)
        password = args.password or getpass.getpass("  New password: ")
        if not password:
            sys.exit("No password entered; aborting.")
        user.set_password(password)
        db.session.commit()
        print(f"{green('Password updated')} for {user.email}.")
    return 0


def cmd_user_admin(args):
    app, db, Submitter = _load_app()
    with app.app_context():
        user = _find_user(Submitter, args.email)
        user.is_admin = args.grant
        db.session.commit()
        status = "granted" if args.grant else "revoked"
        print(f"{green('Admin ' + status)} for {user.email}.")
    return 0


def cmd_user_invite(args):
    app, db, Submitter = _load_app()
    with app.app_context():
        user = _find_user(Submitter, args.email)
        token = user.generate_invite_token()
        db.session.commit()
        _send_invite_email(user.email, token)
        print(f"{green('Invite resent')} to {user.email}.")
    return 0


def cmd_user_delete(args):
    app, db, Submitter = _load_app()
    with app.app_context():
        user = _find_user(Submitter, args.email)
        if not args.yes:
            confirm = (
                input(f"  Delete {user.email}? This cannot be undone. (yes/no): ")
                .strip()
                .lower()
            )
            if confirm != "yes":
                print("  Cancelled.")
                return 0
        db.session.delete(user)
        db.session.commit()
        print(f"{green('Removed')} {args.email}.")
    return 0


# ── venue management ──────────────────────────────────────────────────────────


def _venue_summary(venue, n_events):
    plz = venue.plz.strip() if venue.plz else ""
    parts = [
        f'#{venue.id} "{venue.name}"',
        venue.city.strip() if venue.city and venue.city.strip() else "(no city)",
        plz or "(no plz)",
    ]
    if venue.address and venue.address.strip():
        parts.append(venue.address.strip())
    if venue.coords and venue.coords.strip():
        parts.append("coords")
    if venue.accessibility_token:
        parts.append("token")
    parts.append(f"{n_events} event{'' if n_events == 1 else 's'}")
    return " · ".join(parts)


def _print_merge_stats(stats, totals, warnings):
    for field, value, source_id in stats["backfilled"]:
        print(f"         backfill {field}={value!r} (from #{source_id})")
    for note in stats["accessibility"]:
        print(f"         {note}")
    print(f"         {green('merged')}, {stats['events_repointed']} event(s) repointed")
    totals["merges"] += 1
    totals["deleted"] += stats["venues_deleted"]
    totals["events"] += stats["events_repointed"]
    warnings.extend(stats["warnings"])


def _missing_columns(db, models):
    """Model columns absent from the live schema (DB behind the code)."""
    from sqlalchemy import inspect

    inspector = inspect(db.engine)
    missing = []
    for model in models:
        table = model.__tablename__
        actual = {column["name"] for column in inspector.get_columns(table)}
        missing.extend(
            f"{table}.{column.name}"
            for column in model.__table__.columns
            if column.name not in actual
        )
    return missing


def cmd_venue_dedup(args):
    db_path = None
    if args.db_path:
        db_path = Path(args.db_path).resolve()
        if not db_path.is_file():
            sys.exit(f"No database file at {db_path}")
    app, db, _submitter = _load_app(db_path=db_path)
    with app.app_context():
        from sqlalchemy import func

        from diytracker.models import Event, Venue
        from diytracker.models import VenueAccessibility
        from diytracker.services.venue import (
            find_dedup_candidates,
            merge_group,
            select_survivor,
        )

        # create_all() only creates missing tables — it never adds columns to
        # existing ones. On a DB that predates newer model fields (e.g. the
        # SEO columns event.status and *.updated_at) the dedup queries would
        # die mid-run with OperationalError, so refuse to start instead.
        missing = _missing_columns(db, (Venue, Event, VenueAccessibility))
        if missing:
            sys.exit(
                "Database schema is behind the models; missing column(s): "
                + ", ".join(missing)
                + "\nRun the scripts in migrations/ first "
                "(e.g. migrate_add_seo_columns.py), then rerun dedup."
            )

        def event_counts():
            return dict(
                db.session.query(Event.venue_id, func.count(Event.id))
                .group_by(Event.venue_id)
                .all()
            )

        mode = "APPLY" if args.apply else "DRY RUN (rerun with --apply to merge)"
        print(f"\n  Database: {db.engine.url.database}")
        print(f"  {Venue.query.count()} venues · {mode}\n")

        totals = {"merges": 0, "deleted": 0, "events": 0, "declined": 0}
        warnings = []
        n_auto = 0

        # Auto phase. A merge can backfill a missing city and reveal a new
        # exact match, so under --apply recompute until no auto groups remain.
        while True:
            counts = event_counts()
            auto_groups, pairs = find_dedup_candidates(Venue.query.all())
            n_auto += len(auto_groups)
            for group in auto_groups:
                survivor, losers = select_survivor(group, counts)
                verb = "keep" if args.apply else "would keep"
                print(
                    f"  [auto] {verb} {_venue_summary(survivor, counts.get(survivor.id, 0))}"
                )
                for loser in losers:
                    print(
                        f"         drop {_venue_summary(loser, counts.get(loser.id, 0))}"
                    )
                if args.apply:
                    stats = merge_group(survivor, losers)
                    db.session.commit()
                    _print_merge_stats(stats, totals, warnings)
                print()
            if not args.apply or not auto_groups:
                break

        # Interactive phase: same-name/different-city and similar-name pairs.
        if pairs:
            print(f"  {len(pairs)} pair(s) need confirmation:")
        for pair_a, pair_b, reason in pairs:
            counts = event_counts()
            venue_a = db.session.get(Venue, pair_a.id)
            venue_b = db.session.get(Venue, pair_b.id)
            if venue_a is None or venue_b is None:
                continue  # already merged away earlier in this run
            print(f"\n  [{reason}]")
            print(f"    {_venue_summary(venue_a, counts.get(venue_a.id, 0))}")
            print(f"    {_venue_summary(venue_b, counts.get(venue_b.id, 0))}")
            if not args.apply:
                continue
            survivor, losers = select_survivor([venue_a, venue_b], counts)
            answer = input(f"    Merge into #{survivor.id}? [y/N]: ").strip().lower()
            if answer not in ("y", "yes"):
                totals["declined"] += 1
                print("    skipped")
                continue
            stats = merge_group(survivor, losers)
            db.session.commit()
            _print_merge_stats(stats, totals, warnings)

        print()
        if args.apply:
            print(
                f"  {green('Done.')} {totals['merges']} merge(s), "
                f"{totals['deleted']} venue(s) removed, "
                f"{totals['events']} event(s) repointed, "
                f"{totals['declined']} pair(s) declined."
            )
            for warning in warnings:
                print(f"  WARNING: {warning}")
        else:
            print(
                f"  Dry run: {n_auto} auto group(s), {len(pairs)} pair(s) "
                f"needing confirmation. Rerun with --apply to merge."
            )
    return 0


# ── argument parsing ──────────────────────────────────────────────────────────


def build_parser():
    p = argparse.ArgumentParser(
        prog="manage.py",
        description="diytracker management CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("logs", help="Tail the access (default) or error log")
    sp.add_argument(
        "-e",
        "--error",
        action="store_true",
        help="Show the error log instead of the access log",
    )
    sp.add_argument(
        "-f", "--follow", action="store_true", help="Follow the log (like tail -f)"
    )
    sp.add_argument(
        "-n",
        "--lines",
        type=int,
        default=40,
        help="Number of lines to show (default: 40)",
    )
    sp.set_defaults(func=cmd_logs)

    up = sub.add_parser("user", help="User management")
    usub = up.add_subparsers(dest="user_command", required=True)

    usub.add_parser("list", help="List all users").set_defaults(func=cmd_user_list)

    sp = usub.add_parser("add", help="Create a user and send an invite")
    sp.add_argument("email")
    sp.add_argument(
        "--no-email",
        action="store_true",
        help="Print the invite link instead of emailing it",
    )
    sp.set_defaults(func=cmd_user_add)

    sp = usub.add_parser("passwd", help="Set a user's password")
    sp.add_argument("email")
    sp.add_argument("--password", help="Password (prompted securely if omitted)")
    sp.set_defaults(func=cmd_user_passwd)

    sp = usub.add_parser("admin", help="Grant or revoke a user's admin status")
    sp.add_argument("email")
    group = sp.add_mutually_exclusive_group(required=True)
    group.add_argument("--grant", dest="grant", action="store_true", help="Grant admin")
    group.add_argument(
        "--revoke", dest="grant", action="store_false", help="Revoke admin"
    )
    sp.set_defaults(func=cmd_user_admin)

    sp = usub.add_parser("invite", help="Resend an invite email to a user")
    sp.add_argument("email")
    sp.set_defaults(func=cmd_user_invite)

    sp = usub.add_parser("delete", help="Delete a user")
    sp.add_argument("email")
    sp.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
    sp.set_defaults(func=cmd_user_delete)

    vp = sub.add_parser("venue", help="Venue management")
    vsub = vp.add_subparsers(dest="venue_command", required=True)

    sp = vsub.add_parser(
        "dedup", help="Find and merge duplicate venues (dry-run by default)"
    )
    sp.add_argument(
        "--apply", action="store_true", help="Actually merge (default: dry run)"
    )
    sp.add_argument(
        "--db-path",
        help="Operate on an alternate SQLite database file instead of the app DB",
    )
    sp.set_defaults(func=cmd_venue_dedup)

    return p


def main():
    args = build_parser().parse_args()
    sys.exit(args.func(args) or 0)


if __name__ == "__main__":
    main()
