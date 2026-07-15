#!/usr/bin/env python3
"""diytracker management CLI.

Tail the server logs and manage users. Run it through uv so the project's
dependencies are available:

    uv run python manage.py <command> [options]

Commands:
    logs     Tail the access or error log
    user     Manage users (list / add / passwd / admin / invite / delete)
    venue    Manage venues (dedup)
    event    Manage events (list / status / delete / dedup)
    stats    App statistics (leaderboard / overview)
    db       Database maintenance (backup / vacuum)

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
        print(f"  {'':>3}  {'ADMIN':<6}  {'PROMO':<6}  {'PW':<6}  email")
        print(f"  {'-' * 50}")
        for i, user in enumerate(users, 1):
            admin_flag = "admin" if user.is_admin else ""
            promo_flag = "promo" if user.is_promoter else ""
            pw_flag = "set" if user.password_hash else "NO PW"
            print(
                f"  {i:>3}  {admin_flag:<6}  {promo_flag:<6}  {pw_flag:<6}  {user.email}"
            )
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


def cmd_user_promoter(args):
    app, db, Submitter = _load_app()
    with app.app_context():
        user = _find_user(Submitter, args.email)
        user.is_promoter = args.grant
        db.session.commit()
        status = "granted" if args.grant else "revoked"
        print(f"{green('Promoter ' + status)} for {user.email}.")
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


# ── event management ──────────────────────────────────────────────────────────


EVENT_STATUSES = ("scheduled", "cancelled", "postponed")


def _find_event(db, Event, event_id):
    event = db.session.get(Event, event_id)
    if not event:
        sys.exit(f"No event found with id {event_id}.")
    return event


def _event_line(event):
    venue = event.venue.name if event.venue else "(no venue)"
    who = event.submitter.email if event.submitter else "-"
    return (
        f"  {event.id:>5}  {event.date:%Y-%m-%d}  {event.status:<10}  "
        f"{event.name[:40]:<40}  {venue[:25]:<25}  {who}"
    )


def cmd_event_list(args):
    app, db, _submitter = _load_app()
    with app.app_context():
        from diytracker.models import Event, utcnow

        q = Event.query
        if args.all:
            q = q.order_by(Event.date.desc())
        else:
            q = q.filter(Event.date >= utcnow()).order_by(Event.date.asc())
        if args.lines:
            q = q.limit(args.lines)
        events = q.all()
        if not events:
            print("No events." if args.all else "No upcoming events.")
            return 0
        print()
        scope = "events (newest first)" if args.all else "upcoming events"
        print(f"  {len(events)} {scope}:")
        print(
            f"  {'ID':>5}  {'DATE':<10}  {'STATUS':<10}  {'NAME':<40}  {'VENUE':<25}  SUBMITTER"
        )
        print(f"  {'-' * 100}")
        for event in events:
            print(_event_line(event))
        print()
    return 0


def cmd_event_status(args):
    app, db, _submitter = _load_app()
    with app.app_context():
        from diytracker.models import Event
        from diytracker.services.cache import bust_cache

        event = _find_event(db, Event, args.event_id)
        event.status = args.status
        db.session.commit()
        bust_cache()
        print(f"{green('Status set')} to {args.status} for #{event.id} {event.name!r}.")
    return 0


def cmd_event_delete(args):
    app, db, _submitter = _load_app()
    with app.app_context():
        from diytracker.models import Event
        from diytracker.services.cache import bust_cache

        event = _find_event(db, Event, args.event_id)
        print(_event_line(event))
        if not args.yes:
            confirm = (
                input(f"  Delete event #{event.id}? This cannot be undone. (yes/no): ")
                .strip()
                .lower()
            )
            if confirm != "yes":
                print("  Cancelled.")
                return 0
        db.session.delete(event)
        db.session.commit()
        bust_cache()
        print(f"{green('Removed')} event #{args.event_id}.")
    return 0


def cmd_event_dedup(args):
    app, db, _submitter = _load_app()
    with app.app_context():
        from diytracker.models import Event, utcnow
        from diytracker.services.cache import bust_cache
        from diytracker.services.event_dedup import (
            find_event_dedup_candidates,
            merge_events,
            select_survivor,
        )

        q = Event.query
        if not args.all:
            q = q.filter(Event.date >= utcnow())
        events = q.all()

        mode = "APPLY" if args.apply else "DRY RUN (rerun with --apply to merge)"
        print(f"\n  Database: {db.engine.url.database}")
        print(f"  {len(events)} event(s) scanned · {mode}\n")

        pairs = find_event_dedup_candidates(events)
        if not pairs:
            print("  No candidate duplicates found.\n")
            return 0

        print(f"  {len(pairs)} pair(s) need confirmation:")
        merged = 0
        declined = 0
        for event_a, event_b, shared in pairs:
            event_a = db.session.get(Event, event_a.id)
            event_b = db.session.get(Event, event_b.id)
            if event_a is None or event_b is None:
                continue  # already merged away earlier in this run
            print(f"\n  [shared words: {', '.join(sorted(shared))}]")
            print(_event_line(event_a))
            print(_event_line(event_b))
            if not args.apply:
                continue
            survivor, losers = select_survivor([event_a, event_b])
            answer = input(f"    Merge into #{survivor.id}? [y/N]: ").strip().lower()
            if answer not in ("y", "yes"):
                declined += 1
                print("    skipped")
                continue
            stats = merge_events(survivor, losers)
            db.session.commit()
            bust_cache()
            print(f"    {green('merged')}, {stats['events_deleted']} event(s) removed")
            merged += 1

        print()
        if args.apply:
            print(f"  {green('Done.')} {merged} merge(s), {declined} pair(s) declined.")
        else:
            print("  Dry run: rerun with --apply to merge.")
    return 0


# ── statistics ────────────────────────────────────────────────────────────────


TIMEFRAMES = {
    "7d": "last 7 days",
    "month": "last month",
    "3months": "last 3 months",
    "all": "all time",
}


def _since(timeframe):
    """Start of the given timeframe as naive UTC, or None for all time."""
    from datetime import timedelta

    from dateutil.relativedelta import relativedelta

    from diytracker.models import utcnow

    now = utcnow()
    return {
        "7d": now - timedelta(days=7),
        "month": now - relativedelta(months=1),
        "3months": now - relativedelta(months=3),
        "all": None,
    }[timeframe]


def cmd_stats_leaderboard(args):
    app, db, Submitter = _load_app()
    with app.app_context():
        from sqlalchemy import func

        from diytracker.models import Event

        missing = _missing_columns(db, (Event,))
        if missing:
            sys.exit(
                "Database schema is behind the models; missing column(s): "
                + ", ".join(missing)
                + "\nRun migrations/migrate_add_created_at.py first."
            )

        since = _since(args.timeframe)
        ranked = (
            db.session.query(Submitter.email, func.count(Event.id).label("n"))
            .join(Event, Event.submitter_id == Submitter.id)
            .group_by(Submitter.id)
        )
        unattributed = Event.query.filter(Event.submitter_id.is_(None))
        if since is not None:
            ranked = ranked.filter(Event.created_at >= since)
            unattributed = unattributed.filter(Event.created_at >= since)
        rows = ranked.order_by(func.count(Event.id).desc(), Submitter.email).all()
        n_unattributed = unattributed.count()

        print()
        print(f"  Contributions ({TIMEFRAMES[args.timeframe]}):")
        if not rows and not n_unattributed:
            print("  No contributions in this timeframe.")
            print()
            return 0
        print(f"  {'':>3}  {'EVENTS':>6}  submitter")
        print(f"  {'-' * 50}")
        for i, (email, count) in enumerate(rows, 1):
            print(f"  {i:>3}  {count:>6}  {email}")
        if n_unattributed:
            print(f"  {'':>3}  {n_unattributed:>6}  (no submitter, e.g. scraped)")
        total = sum(count for _email, count in rows) + n_unattributed
        print(f"  {'-' * 50}")
        print(f"  {'':>3}  {total:>6}  total")
        print()
    return 0


def cmd_stats_overview(args):
    app, db, Submitter = _load_app()
    with app.app_context():
        from diytracker.models import Event, ScrapedEvent, SkippedUrl, Venue, utcnow

        now = utcnow()
        total_events = Event.query.count()
        upcoming = Event.query.filter(Event.date >= now).count()
        users = Submitter.query.all()
        n_admins = sum(1 for user in users if user.is_admin)
        n_no_pw = sum(1 for user in users if not user.password_hash)
        pending = ScrapedEvent.query.filter(ScrapedEvent.approved.is_(False)).count()

        print()
        print(f"  Database: {db.engine.url.database}")
        db_file = Path(db.engine.url.database)
        if db_file.is_file():
            print(f"  Size:     {_fmt_size(db_file.stat().st_size)}")
        print()
        print(
            f"  Events:       {total_events} ({upcoming} upcoming, {total_events - upcoming} past)"
        )
        print(f"  Venues:       {Venue.query.count()}")
        print(
            f"  Users:        {len(users)} ({n_admins} admin(s), {n_no_pw} without password)"
        )
        print(f"  Scrape queue: {pending} pending")
        print(f"  Skipped URLs: {SkippedUrl.query.count()}")
        print()
    return 0


# ── database maintenance ──────────────────────────────────────────────────────


def _fmt_size(n_bytes):
    for unit in ("B", "KiB", "MiB", "GiB"):
        if n_bytes < 1024 or unit == "GiB":
            return f"{n_bytes:.1f} {unit}" if unit != "B" else f"{n_bytes} B"
        n_bytes /= 1024


def cmd_db_backup(args):
    import sqlite3
    from datetime import datetime

    app, db, _submitter = _load_app()
    with app.app_context():
        src = Path(db.engine.url.database)
        if not src.is_file():
            sys.exit(f"No database file at {src}")
        if args.dest:
            dest = Path(args.dest).resolve()
        else:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            dest = PROJECT_ROOT / "backups" / f"diytracker-{stamp}.sqlite3"
        dest.parent.mkdir(parents=True, exist_ok=True)
        # sqlite3's online backup API copies a consistent snapshot even while
        # the WAL is live, unlike a plain file copy.
        source_conn = sqlite3.connect(src)
        dest_conn = sqlite3.connect(dest)
        try:
            source_conn.backup(dest_conn)
        finally:
            dest_conn.close()
            source_conn.close()
        print(f"{green('Backed up')} to {dest} ({_fmt_size(dest.stat().st_size)}).")
    return 0


def cmd_db_vacuum(args):
    app, db, _submitter = _load_app()
    with app.app_context():
        path = Path(db.engine.url.database)
        if not path.is_file():
            sys.exit(f"No database file at {path}")
        before = path.stat().st_size
        # VACUUM refuses to run inside a transaction, hence autocommit; the
        # checkpoint folds the WAL back in so the reported size is real.
        with db.engine.connect().execution_options(
            isolation_level="AUTOCOMMIT"
        ) as conn:
            conn.execute(db.text("VACUUM"))
            conn.execute(db.text("PRAGMA wal_checkpoint(TRUNCATE)"))
        after = path.stat().st_size
        print(
            f"{green('Vacuumed')} {path.name}: "
            f"{_fmt_size(before)} -> {_fmt_size(after)}."
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

    sp = usub.add_parser("promoter", help="Grant or revoke a user's promoter status")
    sp.add_argument("email")
    group = sp.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--grant", dest="grant", action="store_true", help="Grant promoter"
    )
    group.add_argument(
        "--revoke", dest="grant", action="store_false", help="Revoke promoter"
    )
    sp.set_defaults(func=cmd_user_promoter)

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

    ep = sub.add_parser("event", help="Event management")
    esub = ep.add_subparsers(dest="event_command", required=True)

    sp = esub.add_parser("list", help="List upcoming events")
    sp.add_argument(
        "-n",
        "--lines",
        type=int,
        default=20,
        help="Number of events to show, 0 for no limit (default: 20)",
    )
    sp.add_argument(
        "--all",
        action="store_true",
        help="Include past events (newest first) instead of upcoming only",
    )
    sp.set_defaults(func=cmd_event_list)

    sp = esub.add_parser("status", help="Set an event's status")
    sp.add_argument("event_id", type=int)
    sp.add_argument("status", choices=EVENT_STATUSES)
    sp.set_defaults(func=cmd_event_status)

    sp = esub.add_parser("delete", help="Delete an event")
    sp.add_argument("event_id", type=int)
    sp.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
    sp.set_defaults(func=cmd_event_delete)

    sp = esub.add_parser(
        "dedup",
        help="Find and merge events whose names share 3+ words on the same date",
    )
    sp.add_argument(
        "--apply", action="store_true", help="Actually merge (default: dry run)"
    )
    sp.add_argument(
        "--all",
        action="store_true",
        help="Scan past events too, instead of upcoming only",
    )
    sp.set_defaults(func=cmd_event_dedup)

    stp = sub.add_parser("stats", help="App statistics")
    ssub = stp.add_subparsers(dest="stats_command", required=True)

    sp = ssub.add_parser("leaderboard", help="Contributions per submitter")
    sp.add_argument(
        "-t",
        "--timeframe",
        choices=sorted(TIMEFRAMES),
        default="all",
        help="Only count events contributed in this window (default: all)",
    )
    sp.set_defaults(func=cmd_stats_leaderboard)

    ssub.add_parser("overview", help="Event/venue/user/queue totals").set_defaults(
        func=cmd_stats_overview
    )

    dp = sub.add_parser("db", help="Database maintenance")
    dsub = dp.add_subparsers(dest="db_command", required=True)

    sp = dsub.add_parser("backup", help="Snapshot the database (WAL-safe)")
    sp.add_argument(
        "--dest",
        help="Backup file path (default: backups/diytracker-<timestamp>.sqlite3)",
    )
    sp.set_defaults(func=cmd_db_backup)

    dsub.add_parser("vacuum", help="Compact the database file").set_defaults(
        func=cmd_db_vacuum
    )

    return p


def main():
    args = build_parser().parse_args()
    sys.exit(args.func(args) or 0)


if __name__ == "__main__":
    main()
