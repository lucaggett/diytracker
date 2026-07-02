#!/usr/bin/env python3
"""diytracker management CLI.

Start/stop the gunicorn server, inspect its status, follow logs, and manage
users — all from one place. Run it through uv so the project's dependencies
are available:

    uv run python manage.py <command> [options]

Commands:
    start    Start the gunicorn server (daemonized by default)
    stop     Stop the running server
    restart  Stop then start the server
    status   Show whether the server is running, plus per-process info
    logs     Tail the access or error log
    user     Manage users (list / add / passwd / admin / invite / delete)
"""

import argparse
import getpass
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
PIDFILE = PROJECT_ROOT / "instance" / "gunicorn.pid"
GUNICORN_CONF = PROJECT_ROOT / "gunicorn_conf.py"
APP_MODULE = "app:app"
ACCESS_LOG = PROJECT_ROOT / "logs" / "access_log_diytracker"
ERROR_LOG = PROJECT_ROOT / "logs" / "error_log_diytracker"


# ── output helpers ────────────────────────────────────────────────────────────


def _use_color():
    return sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def _c(text, code):
    return f"\033[{code}m{text}\033[0m" if _use_color() else text


def green(t):
    return _c(t, "32")


def red(t):
    return _c(t, "31")


def bold(t):
    return _c(t, "1")


def dim(t):
    return _c(t, "2")


# ── process helpers ───────────────────────────────────────────────────────────


def _gunicorn_bin():
    """Path to gunicorn inside the active venv, falling back to PATH."""
    candidate = Path(sys.executable).with_name("gunicorn")
    return str(candidate) if candidate.exists() else "gunicorn"


def _read_pid():
    try:
        return int(PIDFILE.read_text().strip())
    except (FileNotFoundError, ValueError):
        return None


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _running_pid():
    """Return the master PID if the server is running, else None."""
    pid = _read_pid()
    return pid if pid and _pid_alive(pid) else None


def _bind_addr():
    """Read the `bind` setting from gunicorn_conf.py without importing the app."""
    ns = {}
    try:
        exec(compile(GUNICORN_CONF.read_text(), str(GUNICORN_CONF), "exec"), ns)
    except Exception:
        return None
    return ns.get("bind")


def _ps_processes(master_pid):
    """Return [{pid, ppid, cpu, mem, rss, etime}] for the master and its workers."""
    fmt = "pid=,ppid=,pcpu=,pmem=,rss=,etime="
    try:
        out = subprocess.run(
            ["ps", "-o", fmt, "--pid", str(master_pid), "--ppid", str(master_pid)],
            capture_output=True,
            text=True,
            check=False,
        ).stdout
    except FileNotFoundError:
        return []
    rows = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 6:
            continue
        pid, ppid, cpu, mem, rss, etime = parts[:6]
        rows.append(
            {
                "pid": int(pid),
                "ppid": int(ppid),
                "cpu": cpu,
                "mem": mem,
                "rss": int(rss),
                "etime": etime,
            }
        )
    return rows


# ── server lifecycle ──────────────────────────────────────────────────────────


def cmd_start(args):
    pid = _running_pid()
    if pid:
        print(f"Already running (master PID {pid}). Use 'restart' to reload.")
        return 1

    PIDFILE.parent.mkdir(parents=True, exist_ok=True)
    (PROJECT_ROOT / "logs").mkdir(exist_ok=True)

    cmd = [
        _gunicorn_bin(),
        "-c",
        str(GUNICORN_CONF),
        APP_MODULE,
        "--chdir",
        str(PROJECT_ROOT),
        "--pid",
        str(PIDFILE),
    ]

    if args.foreground:
        return subprocess.run(cmd, cwd=PROJECT_ROOT).returncode

    cmd.append("--daemon")
    subprocess.run(cmd, cwd=PROJECT_ROOT, check=True)

    # Daemonized gunicorn detaches immediately; wait for the pidfile to appear.
    for _ in range(50):
        pid = _running_pid()
        if pid:
            print(f"{green('Started')} (master PID {pid}).")
            return 0
        time.sleep(0.1)

    print(red("Failed to start.") + f" Check the error log:\n  {ERROR_LOG}")
    return 1


def cmd_stop(args):
    pid = _running_pid()
    if not pid:
        print("Not running.")
        PIDFILE.unlink(missing_ok=True)
        return 0

    os.kill(pid, signal.SIGTERM)
    for _ in range(args.timeout * 10):
        if not _pid_alive(pid):
            break
        time.sleep(0.1)
    else:
        print(f"Did not stop within {args.timeout}s; sending SIGKILL.")
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    PIDFILE.unlink(missing_ok=True)
    print(f"{green('Stopped')} (was PID {pid}).")
    return 0


def cmd_restart(args):
    cmd_stop(argparse.Namespace(timeout=args.timeout))
    return cmd_start(argparse.Namespace(foreground=False))


def cmd_status(args):
    pid = _running_pid()
    if not pid:
        print(f"diytracker: {red('stopped')}")
        return 0

    print(f"diytracker: {green('running')}")
    bind = _bind_addr()
    if bind:
        print(f"  {dim('bind')}     {bind}")
    print(f"  {dim('master')}   PID {pid}")

    procs = _ps_processes(pid)
    workers = [p for p in procs if p["pid"] != pid]
    print(f"  {dim('workers')}  {len(workers)}")

    master = next((p for p in procs if p["pid"] == pid), None)
    if master:
        print(f"  {dim('uptime')}   {master['etime']}")

    if procs:
        print()
        print(
            bold(
                f"  {'PID':>7}  {'ROLE':<7}  {'CPU%':>5}  {'MEM%':>5}  {'RSS':>8}  UPTIME"
            )
        )
        for p in sorted(procs, key=lambda x: (x["pid"] != pid, x["pid"])):
            role = "master" if p["pid"] == pid else "worker"
            rss = f"{p['rss'] / 1024:.0f}M"
            print(
                f"  {p['pid']:>7}  {role:<7}  {p['cpu']:>5}  {p['mem']:>5}  {rss:>8}  {p['etime']}"
            )
    return 0


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
        from app import app, db
        from models import Submitter
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

    sp = sub.add_parser("start", help="Start the gunicorn server")
    sp.add_argument(
        "-f",
        "--foreground",
        action="store_true",
        help="Run in the foreground instead of daemonizing",
    )
    sp.set_defaults(func=cmd_start)

    sp = sub.add_parser("stop", help="Stop the running server")
    sp.add_argument(
        "--timeout",
        type=int,
        default=10,
        help="Seconds to wait for graceful shutdown before SIGKILL (default: 10)",
    )
    sp.set_defaults(func=cmd_stop)

    sp = sub.add_parser("restart", help="Restart the server")
    sp.add_argument(
        "--timeout",
        type=int,
        default=10,
        help="Seconds to wait for graceful shutdown before SIGKILL (default: 10)",
    )
    sp.set_defaults(func=cmd_restart)

    sub.add_parser("status", help="Show server status and process info").set_defaults(
        func=cmd_status
    )

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
