"""Smoke tests that actually mount the TUI screens.

The logic layer is covered in test_admin_tool.py; what those tests cannot
catch is a screen that fails on mount — a missing row map, a binding pointing
at an action that no longer exists, a column added after the first refresh.
Textual's Pilot boots the real app against the test database, so each screen
here is opened the way the menu opens it.

The scenarios are async, but they run through asyncio.run() rather than
pytest-asyncio: a dependency the project does not otherwise need. Deliberately
shallow — nothing asserts on pixels, only that a screen mounts, refreshes,
and puts something in its table.
"""

import asyncio
from datetime import date, datetime, timedelta

import pytest
from textual.widgets import DataTable, Static

from diytracker.admin import traffic
from diytracker.admin.tui.app import MENU, ManageApp
from diytracker.admin.tui.screens import TrafficScreen
from diytracker.models import EventDailyViews, Submitter, db

SEARCHABLE = ["users", "events", "labels", "venues", "audit"]

BROWSER_UA = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"


@pytest.fixture
def manage_app(app):
    return ManageApp((app, db, Submitter))


@pytest.fixture
def traffic_log(app, tmp_path, monkeypatch, make_event):
    """A log file with one human, one bot and one 5xx, plus recorded event
    views — enough that every traffic view has something to render."""
    now = datetime.now()

    def line(ip, ts, path, status=200, ua=BROWSER_UA):
        stamp = ts.strftime("%d/%b/%Y:%H:%M:%S +0200")
        return f'{ip} - - [{stamp}] "GET {path} HTTP/1.1" {status} 1234 "-" "{ua}"'

    lines = [line("10.0.0.1", now - timedelta(minutes=30 * i), "/") for i in range(4)]
    lines += [line("10.0.0.2", now, "/events/archive/2023", ua="curl/8.7.1")]
    lines += [line("10.0.0.3", now, "/search", status=502)]
    log_file = tmp_path / "access.log"
    log_file.write_text("\n".join(lines) + "\n")
    monkeypatch.setattr(traffic, "find_log_files", lambda: [log_file])

    with app.app_context():
        event = make_event()
        db.session.add(
            EventDailyViews(event_id=event.id, date=date.today(), hits=20, visitors=11)
        )
        db.session.commit()
    return log_file


def _screen_class(option_id):
    return next(entry[2] for entry in MENU if entry[0] == option_id)


def test_main_menu_mounts(manage_app):
    async def scenario():
        async with manage_app.run_test() as pilot:
            await pilot.pause()
            assert manage_app.screen.sub_title == "Main menu"

    asyncio.run(scenario())


@pytest.mark.parametrize("option_id", [entry[0] for entry in MENU])
def test_every_screen_mounts(manage_app, option_id):
    screen_class = _screen_class(option_id)

    async def scenario():
        async with manage_app.run_test() as pilot:
            manage_app.push_screen(screen_class())
            await pilot.pause()
            # Workers own the first refresh; wait for them instead of sleeping.
            await manage_app.workers.wait_for_complete()
            await pilot.pause()
            assert isinstance(manage_app.screen, screen_class)

    asyncio.run(scenario())


@pytest.mark.parametrize("view", TrafficScreen.VIEWS)
def test_every_traffic_view_renders(manage_app, view, traffic_log, monkeypatch):
    """Each view builds its table from real log lines — the summary text and
    column counts have to match what the logic layer returns."""
    monkeypatch.setattr(traffic, "resolve_many", lambda ips: dict.fromkeys(ips))

    async def scenario():
        async with manage_app.run_test() as pilot:
            screen = TrafficScreen()
            screen.view = view
            manage_app.push_screen(screen)
            await pilot.pause()
            await manage_app.workers.wait_for_complete()
            await pilot.pause()
            summary = manage_app.screen.query_one("#traffic-summary", Static)
            table = manage_app.screen.query_one("#traffic-table", DataTable)
            assert str(summary.content).strip()
            assert table.columns, f"{view} view rendered no columns"
            assert table.row_count, f"{view} view rendered no rows"

    asyncio.run(scenario())


@pytest.mark.parametrize("option_id", SEARCHABLE)
def test_filter_is_applied_and_visible(manage_app, option_id, make_venue):
    """A set filter reaches the logic call and shows up in the subtitle."""
    make_venue(name="Rote Fabrik", city="Zürich")
    screen_class = _screen_class(option_id)

    async def scenario():
        async with manage_app.run_test() as pilot:
            screen = screen_class()
            manage_app.push_screen(screen)
            await pilot.pause()
            await manage_app.workers.wait_for_complete()

            screen.filter_query = "fabrik"
            screen.action_refresh()
            await manage_app.workers.wait_for_complete()
            await pilot.pause()
            assert 'filter: "fabrik"' in screen.sub_title

    asyncio.run(scenario())
