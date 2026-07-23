"""The Textual app shell and main menu."""

import asyncio

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, Header, OptionList
from textual.widgets.option_list import Option

from diytracker.admin.tui.screens import (
    DatabaseScreen,
    EventDedupScreen,
    EventsScreen,
    GenreDedupScreen,
    LabelsScreen,
    LogsScreen,
    StatsScreen,
    TrafficScreen,
    UsersScreen,
    VenueDedupScreen,
)

MENU = [
    ("users", "Users", UsersScreen),
    ("events", "Events", EventsScreen),
    ("labels", "Labels", LabelsScreen),
    ("venue-dedup", "Venue dedup", VenueDedupScreen),
    ("event-dedup", "Event dedup", EventDedupScreen),
    ("genre-dedup", "Genre dedup", GenreDedupScreen),
    ("stats", "Stats", StatsScreen),
    ("traffic", "Traffic", TrafficScreen),
    ("db", "Database", DatabaseScreen),
    ("logs", "Logs", LogsScreen),
]


class MainScreen(Screen):
    def compose(self) -> ComposeResult:
        yield Header()
        yield OptionList(
            *[Option(label, id=option_id) for option_id, label, _screen in MENU],
            id="menu",
        )
        yield Footer()

    def on_mount(self):
        self.sub_title = "Main menu"
        self.query_one("#menu", OptionList).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected):
        for option_id, _label, screen_class in MENU:
            if option_id == event.option.id:
                self.app.push_screen(screen_class())
                return


class ManageApp(App):
    """diytracker admin TUI. Expects a pre-loaded Flask app context tuple
    (flask_app, db, Submitter) from diytracker.admin.core.load_app()."""

    TITLE = "diytracker management"
    CSS_PATH = "manage.tcss"
    BINDINGS = [Binding("q", "quit", "Quit")]

    def __init__(self, ctx):
        super().__init__()
        self.flask_app, self.db, self.Submitter = ctx

    def on_mount(self):
        self.push_screen(MainScreen())

    async def run_db(self, fn, *args):
        """Run a logic-layer call in a thread, inside a fresh app context.

        Logic functions return plain dataclasses (never ORM objects), so
        results stay valid after the context closes.
        """

        def inner():
            with self.flask_app.app_context():
                return fn(*args)

        return await asyncio.to_thread(inner)
