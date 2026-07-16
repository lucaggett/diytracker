#!/usr/bin/env python3
"""diytracker management tool.

Run with no arguments for the interactive TUI (users, events, dedup,
stats, database, logs). A minimal CLI remains for scripted usage:

    uv run python manage.py                      # TUI
    uv run python manage.py <command> [options]  # CLI

CLI commands:
    logs     Tail the access or error log
    user     Manage users (list / add / passwd / admin / promoter /
             invite / delete); invites go out in Schwiizerdütsch by
             default (--lang gsw|en|fr)
    db       Database maintenance (backup / vacuum)

Venue/event dedup, event editing, and stats live in the TUI now. All
logic sits in diytracker/admin/, so re-adding a CLI command is a thin
wrapper if ever needed. The gunicorn server itself runs under systemd —
see deploy/README.md.
"""

import argparse
import getpass
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from diytracker.admin.core import ACCESS_LOG, ERROR_LOG, AdminError, fmt_size, load_app  # noqa: E402
from diytracker.admin.invites import (  # noqa: E402
    DEFAULT_INVITE_LANG,
    INVITE_LANGS,
    invite_link,
    send_invite_email,
)


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


def _with_context():
    """Load the app (exiting on config errors) and enter its context."""
    app, db, _submitter = load_app()
    return app


def cmd_user_list(args):
    from diytracker.admin import users

    app = _with_context()
    with app.app_context():
        rows = users.list_users()
    if not rows:
        print("No users.")
        return 0
    print()
    print(f"  {'':>3}  {'ADMIN':<6}  {'PROMO':<6}  {'PW':<6}  email")
    print(f"  {'-' * 50}")
    for i, row in enumerate(rows, 1):
        admin_flag = "admin" if row.is_admin else ""
        promo_flag = "promo" if row.is_promoter else ""
        pw_flag = "set" if row.has_password else "NO PW"
        print(f"  {i:>3}  {admin_flag:<6}  {promo_flag:<6}  {pw_flag:<6}  {row.email}")
    print()
    return 0


def _deliver_invite(email, token, lang, no_email):
    if no_email:
        print(f"  Invite link: {invite_link(token)}")
        return
    send_invite_email(email, token, lang)
    print(f"  Invite email sent to {email} ({lang})")


def cmd_user_add(args):
    from diytracker.admin import users

    app = _with_context()
    with app.app_context():
        token = users.add_user(args.email)
    print(f"{green('Created')} user {args.email}.")
    _deliver_invite(args.email, token, args.lang, args.no_email)
    return 0


def cmd_user_passwd(args):
    from diytracker.admin import users

    app = _with_context()
    password = args.password or getpass.getpass("  New password: ")
    with app.app_context():
        users.set_password(args.email, password)
    print(f"{green('Password updated')} for {args.email}.")
    return 0


def cmd_user_admin(args):
    from diytracker.admin import users

    app = _with_context()
    with app.app_context():
        users.set_flag(args.email, "is_admin", args.grant)
    status = "granted" if args.grant else "revoked"
    print(f"{green('Admin ' + status)} for {args.email}.")
    return 0


def cmd_user_promoter(args):
    from diytracker.admin import users

    app = _with_context()
    with app.app_context():
        users.set_flag(args.email, "is_promoter", args.grant)
    status = "granted" if args.grant else "revoked"
    print(f"{green('Promoter ' + status)} for {args.email}.")
    return 0


def cmd_user_invite(args):
    from diytracker.admin import users

    app = _with_context()
    with app.app_context():
        token = users.reissue_invite(args.email)
    _deliver_invite(args.email, token, args.lang, no_email=False)
    print(f"{green('Invite resent')} to {args.email}.")
    return 0


def cmd_user_delete(args):
    from diytracker.admin import users

    app = _with_context()
    if not args.yes:
        confirm = (
            input(f"  Delete {args.email}? This cannot be undone. (yes/no): ")
            .strip()
            .lower()
        )
        if confirm != "yes":
            print("  Cancelled.")
            return 0
    with app.app_context():
        users.delete_user(args.email)
    print(f"{green('Removed')} {args.email}.")
    return 0


# ── database maintenance ──────────────────────────────────────────────────────


def cmd_db_backup(args):
    from diytracker.admin import db_tools

    app = _with_context()
    with app.app_context():
        dest, size = db_tools.backup(args.dest)
    print(f"{green('Backed up')} to {dest} ({fmt_size(size)}).")
    return 0


def cmd_db_vacuum(args):
    from diytracker.admin import db_tools

    app = _with_context()
    with app.app_context():
        name, before, after = db_tools.vacuum()
    print(f"{green('Vacuumed')} {name}: {fmt_size(before)} -> {fmt_size(after)}.")
    return 0


# ── argument parsing ──────────────────────────────────────────────────────────


def build_parser():
    p = argparse.ArgumentParser(
        prog="manage.py",
        description="diytracker management CLI (run without arguments for the TUI)",
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
    sp.add_argument(
        "--lang",
        choices=INVITE_LANGS,
        default=DEFAULT_INVITE_LANG,
        help=f"Invite email language (default: {DEFAULT_INVITE_LANG})",
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
    sp.add_argument(
        "--lang",
        choices=INVITE_LANGS,
        default=DEFAULT_INVITE_LANG,
        help=f"Invite email language (default: {DEFAULT_INVITE_LANG})",
    )
    sp.set_defaults(func=cmd_user_invite)

    sp = usub.add_parser("delete", help="Delete a user")
    sp.add_argument("email")
    sp.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
    sp.set_defaults(func=cmd_user_delete)

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
    if len(sys.argv) == 1:
        # Lazy import: keep plain CLI invocations free of the textual import.
        from diytracker.admin.tui.app import ManageApp

        try:
            ctx = load_app()
        except AdminError as exc:
            sys.exit(str(exc))
        ManageApp(ctx).run()
        return
    args = build_parser().parse_args()
    try:
        sys.exit(args.func(args) or 0)
    except AdminError as exc:
        sys.exit(str(exc))


if __name__ == "__main__":
    main()
