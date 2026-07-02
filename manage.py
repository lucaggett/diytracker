#!/usr/bin/env python3
"""diytracker management CLI.

Tail the server logs and manage users. Run it through uv so the project's
dependencies are available:

    uv run python manage.py <command> [options]

Commands:
    logs     Tail the access or error log
    user     Manage users (list / add / passwd / admin / invite / delete)

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


def _load_app():
    """Import the Flask app lazily (it requires .env and starts a scraper thread)."""
    os.chdir(PROJECT_ROOT)
    sys.path.insert(0, str(PROJECT_ROOT))
    try:
        from diytracker.app import app, db
        from diytracker.models import Submitter
    except KeyError as exc:
        sys.exit(f"Missing environment variable {exc}. Is your .env present?")
    return app, db, Submitter


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

    return p


def main():
    args = build_parser().parse_args()
    sys.exit(args.func(args) or 0)


if __name__ == "__main__":
    main()
