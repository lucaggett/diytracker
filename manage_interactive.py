#!/usr/bin/env python3
"""diytracker interactive management CLI.

A menu-driven wrapper around manage.py: instead of remembering subcommands
and flags, pick actions from numbered menus and answer a few prompts. Every
action calls the exact same cmd_* function manage.py's argparse dispatch
would call, so behavior (confirmation prompts, dry-run defaults, output) is
identical — this is just another way to reach it.

    uv run python manage_interactive.py
"""

from types import SimpleNamespace

import manage


def _prompt(label, default=None):
    suffix = f" [{default}]" if default else ""
    raw = input(f"  {label}{suffix}: ").strip()
    return raw or (default or "")


def _prompt_bool(label, default=False):
    hint = "Y/n" if default else "y/N"
    raw = input(f"  {label}? [{hint}]: ").strip().lower()
    if not raw:
        return default
    return raw in ("y", "yes")


def _prompt_int(label, default=None):
    while True:
        raw = _prompt(label, str(default) if default is not None else None)
        try:
            return int(raw)
        except ValueError:
            print("  Please enter a number.")


def _prompt_choice(label, choices):
    choices = list(choices)
    print(f"  {label}:")
    for i, choice in enumerate(choices, 1):
        print(f"    {i}. {choice}")
    while True:
        raw = input(f"  Choose 1-{len(choices)}: ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(choices):
            return choices[int(raw) - 1]
        print("  Invalid choice.")


def _menu(title, options):
    """options: list of (label, callable-with-no-args). Loops until 0/blank."""
    while True:
        print(f"\n=== {title} ===")
        for i, (label, _fn) in enumerate(options, 1):
            print(f"  {i}. {label}")
        print("  0. Back")
        raw = input("  > ").strip()
        if raw in ("0", ""):
            return
        if not raw.isdigit() or not (1 <= int(raw) <= len(options)):
            print("  Invalid choice.")
            continue
        _, fn = options[int(raw) - 1]
        try:
            fn()
        except SystemExit as exc:
            # cmd_* functions call sys.exit() on user errors (bad email,
            # missing file, ...) — surface that as a message, not a crash.
            if exc.code not in (None, 0):
                print(f"  Error: {exc.code}")
        except KeyboardInterrupt:
            print("\n  Cancelled.")


# ── logs ──────────────────────────────────────────────────────────────────────


def _do_logs():
    error = _prompt_bool("Show the error log instead of the access log")
    follow = _prompt_bool("Follow the log (like tail -f)")
    lines = _prompt_int("Number of lines to show", default=40)
    manage.cmd_logs(SimpleNamespace(error=error, follow=follow, lines=lines))


# ── users ─────────────────────────────────────────────────────────────────────


def _do_user_list():
    manage.cmd_user_list(SimpleNamespace())


def _do_user_add():
    email = _prompt("Email")
    send_email = _prompt_bool("Send invite email", default=True)
    manage.cmd_user_add(SimpleNamespace(email=email, no_email=not send_email))


def _do_user_passwd():
    email = _prompt("Email")
    manage.cmd_user_passwd(SimpleNamespace(email=email, password=None))


def _do_user_admin():
    email = _prompt("Email")
    grant = _prompt_bool("Grant admin (no = revoke)", default=True)
    manage.cmd_user_admin(SimpleNamespace(email=email, grant=grant))


def _do_user_promoter():
    email = _prompt("Email")
    grant = _prompt_bool("Grant promoter (no = revoke)", default=True)
    manage.cmd_user_promoter(SimpleNamespace(email=email, grant=grant))


def _do_user_invite():
    email = _prompt("Email")
    manage.cmd_user_invite(SimpleNamespace(email=email))


def _do_user_delete():
    email = _prompt("Email")
    manage.cmd_user_delete(SimpleNamespace(email=email, yes=False))


def _user_menu():
    _menu(
        "User management",
        [
            ("List users", _do_user_list),
            ("Add user (send invite)", _do_user_add),
            ("Set password", _do_user_passwd),
            ("Grant/revoke admin", _do_user_admin),
            ("Grant/revoke promoter", _do_user_promoter),
            ("Resend invite", _do_user_invite),
            ("Delete user", _do_user_delete),
        ],
    )


# ── venues ────────────────────────────────────────────────────────────────────


def _do_venue_dedup():
    apply_ = _prompt_bool("Apply merges (no = dry run)")
    db_path = _prompt("Alternate DB path (blank = app DB)") or None
    manage.cmd_venue_dedup(SimpleNamespace(apply=apply_, db_path=db_path))


def _venue_menu():
    _menu("Venue management", [("Find & merge duplicate venues", _do_venue_dedup)])


# ── events ────────────────────────────────────────────────────────────────────


def _do_event_list():
    all_events = _prompt_bool("Include past events")
    lines = _prompt_int("Number to show (0 = no limit)", default=20)
    manage.cmd_event_list(SimpleNamespace(all=all_events, lines=lines))


def _do_event_status():
    event_id = _prompt_int("Event id")
    status = _prompt_choice("New status", manage.EVENT_STATUSES)
    manage.cmd_event_status(SimpleNamespace(event_id=event_id, status=status))


def _do_event_delete():
    event_id = _prompt_int("Event id")
    manage.cmd_event_delete(SimpleNamespace(event_id=event_id, yes=False))


def _do_event_dedup():
    apply_ = _prompt_bool("Apply merges (no = dry run)")
    all_events = _prompt_bool("Include past events")
    manage.cmd_event_dedup(SimpleNamespace(apply=apply_, all=all_events))


def _event_menu():
    _menu(
        "Event management",
        [
            ("List events", _do_event_list),
            ("Set event status", _do_event_status),
            ("Delete event", _do_event_delete),
            ("Find & merge duplicate events", _do_event_dedup),
        ],
    )


# ── stats ─────────────────────────────────────────────────────────────────────


def _do_stats_leaderboard():
    timeframe = _prompt_choice("Timeframe", sorted(manage.TIMEFRAMES))
    manage.cmd_stats_leaderboard(SimpleNamespace(timeframe=timeframe))


def _do_stats_overview():
    manage.cmd_stats_overview(SimpleNamespace())


def _stats_menu():
    _menu(
        "Statistics",
        [
            ("Leaderboard", _do_stats_leaderboard),
            ("Overview", _do_stats_overview),
        ],
    )


# ── database ──────────────────────────────────────────────────────────────────


def _do_db_backup():
    dest = _prompt("Backup destination (blank = default)") or None
    manage.cmd_db_backup(SimpleNamespace(dest=dest))


def _do_db_vacuum():
    manage.cmd_db_vacuum(SimpleNamespace())


def _db_menu():
    _menu(
        "Database maintenance",
        [
            ("Backup", _do_db_backup),
            ("Vacuum", _do_db_vacuum),
        ],
    )


def main():
    _menu(
        "diytracker management",
        [
            ("Logs", _do_logs),
            ("Users", _user_menu),
            ("Venues", _venue_menu),
            ("Events", _event_menu),
            ("Stats", _stats_menu),
            ("Database", _db_menu),
        ],
    )
    print("Bye.")


if __name__ == "__main__":
    main()
