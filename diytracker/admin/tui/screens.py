"""TUI screens: one per section of the old manage.py command tree."""

import asyncio

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    Footer,
    Header,
    Input,
    RichLog,
    Static,
)

from diytracker.admin import (
    db_tools,
    events,
    invites,
    labels,
    queue_review,
    stats,
    traffic,
    users,
    venues,
)
from diytracker.admin.core import ACCESS_LOG, ERROR_LOG, AdminError, fmt_size, load_app
from diytracker.admin.tui.modals import (
    ChoiceModal,
    ConfirmModal,
    MessageModal,
    PasswordModal,
    PromptModal,
)

LANG_CHOICES = [(lang, invites.LANG_LABELS[lang]) for lang in invites.INVITE_LANGS]


class AdminScreen(Screen):
    """Base: escape pops back to the menu; helpers for DB calls + errors."""

    BINDINGS = [Binding("escape", "back", "Back")]

    def action_back(self):
        self.app.pop_screen()

    async def run_db(self, fn, *args):
        return await self.app.run_db(fn, *args)

    def show_error(self, exc):
        self.notify(str(exc), severity="error", timeout=8)


# ── users ─────────────────────────────────────────────────────────────────────


class UsersScreen(AdminScreen):
    BINDINGS = AdminScreen.BINDINGS + [
        Binding("a", "add", "Add"),
        Binding("p", "passwd", "Password"),
        Binding("i", "invite", "Re-invite"),
        Binding("m", "toggle_admin", "Admin"),
        Binding("o", "toggle_promoter", "Promoter"),
        Binding("d", "delete", "Delete"),
        Binding("r", "refresh", "Refresh"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        yield DataTable(cursor_type="row")
        yield Footer()

    def on_mount(self):
        self.sub_title = "Users"
        table = self.query_one(DataTable)
        table.add_columns("ADMIN", "PROMO", "PW", "EMAIL")
        self.action_refresh()

    def _selected_email(self):
        table = self.query_one(DataTable)
        if table.row_count == 0:
            return None
        key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
        return key.value

    @work(exclusive=True)
    async def action_refresh(self):
        rows = await self.run_db(users.list_users)
        table = self.query_one(DataTable)
        table.clear()
        for row in rows:
            table.add_row(
                "admin" if row.is_admin else "",
                "promo" if row.is_promoter else "",
                "set" if row.has_password else "NO PW",
                row.email,
                key=row.email,
            )

    async def _pick_language(self, title, with_link_option):
        checkbox = (
            "Don't send an email — just show the invite link"
            if with_link_option
            else None
        )
        result = await self.app.push_screen_wait(
            ChoiceModal(title, LANG_CHOICES, checkbox_label=checkbox)
        )
        return result  # (lang, no_email) or None

    async def _deliver_invite(self, email, token, lang, no_email):
        if no_email:
            await self.app.push_screen_wait(
                MessageModal(
                    f"Invite link for {email}",
                    invites.invite_link(token) + "\n\n(valid for 7 days)",
                )
            )
            return
        await self.run_db(invites.send_invite_email, email, token, lang)
        self.notify(f"Invite sent to {email} ({lang}).")

    @work
    async def action_add(self):
        email = await self.app.push_screen_wait(
            PromptModal("Add user", placeholder="email@example.com")
        )
        if not email:
            return
        picked = await self._pick_language(
            f"Invite language for {email}", with_link_option=True
        )
        if picked is None:
            return
        lang, no_email = picked
        try:
            token = await self.run_db(users.add_user, email)
            self.notify(f"Created user {email}.")
            await self._deliver_invite(email, token, lang, no_email)
        except (AdminError, Exception) as exc:
            self.show_error(exc)
        self.action_refresh()

    @work
    async def action_invite(self):
        email = self._selected_email()
        if not email:
            return
        picked = await self._pick_language(
            f"Invite language for {email}", with_link_option=True
        )
        if picked is None:
            return
        lang, no_email = picked
        try:
            token = await self.run_db(users.reissue_invite, email)
            await self._deliver_invite(email, token, lang, no_email)
        except (AdminError, Exception) as exc:
            self.show_error(exc)

    @work
    async def action_passwd(self):
        email = self._selected_email()
        if not email:
            return
        password = await self.app.push_screen_wait(
            PasswordModal(f"Set password for {email}")
        )
        if password is None:
            return
        try:
            await self.run_db(users.set_password, email, password)
            self.notify(f"Password updated for {email}.")
        except AdminError as exc:
            self.show_error(exc)
        self.action_refresh()

    async def _toggle(self, flag, label):
        email = self._selected_email()
        if not email:
            return
        try:
            rows = {row.email: row for row in await self.run_db(users.list_users)}
            current = getattr(rows[email], flag)
            row = await self.run_db(users.set_flag, email, flag, not current)
            state = "granted" if getattr(row, flag) else "revoked"
            self.notify(f"{label} {state} for {email}.")
        except (AdminError, KeyError) as exc:
            self.show_error(exc)
        self.action_refresh()

    @work
    async def action_toggle_admin(self):
        await self._toggle("is_admin", "Admin")

    @work
    async def action_toggle_promoter(self):
        await self._toggle("is_promoter", "Promoter")

    @work
    async def action_delete(self):
        email = self._selected_email()
        if not email:
            return
        confirmed = await self.app.push_screen_wait(
            ConfirmModal(f"Delete {email}? This cannot be undone.", yes_label="Delete")
        )
        if not confirmed:
            return
        try:
            await self.run_db(users.delete_user, email)
            self.notify(f"Removed {email}.")
        except AdminError as exc:
            self.show_error(exc)
        self.action_refresh()


# ── labels ────────────────────────────────────────────────────────────────────


class LabelsScreen(AdminScreen):
    BINDINGS = AdminScreen.BINDINGS + [
        Binding("a", "add", "Add"),
        Binding("n", "rename", "Rename"),
        Binding("e", "edit_description", "Description"),
        Binding("o", "reassign", "Owner"),
        Binding("d", "delete", "Delete"),
        Binding("r", "refresh", "Refresh"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        yield DataTable(cursor_type="row")
        yield Footer()

    def on_mount(self):
        self.sub_title = "Labels"
        table = self.query_one(DataTable)
        table.add_columns("ID", "NAME", "SLUG", "EVENTS", "LOGO", "PROMOTER")
        self.action_refresh()

    def _selected_id(self):
        table = self.query_one(DataTable)
        if table.row_count == 0:
            return None
        key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
        return int(key.value)

    def _selected_row(self):
        label_id = self._selected_id()
        return self._rows.get(label_id) if label_id is not None else None

    @work(exclusive=True)
    async def action_refresh(self):
        rows = await self.run_db(labels.list_labels)
        self._rows = {row.id: row for row in rows}
        table = self.query_one(DataTable)
        table.clear()
        for row in rows:
            table.add_row(
                str(row.id),
                row.name,
                row.slug,
                str(row.n_events),
                "logo" if row.has_logo else "",
                row.promoter,
                key=str(row.id),
            )

    async def _pick_promoter(self, title):
        """Choice of promoter accounts; returns an email or None."""
        promoters = [
            row.email for row in await self.run_db(users.list_users) if row.is_promoter
        ]
        if not promoters:
            self.notify("No promoter accounts — grant the flag in Users first.")
            return None
        picked = await self.app.push_screen_wait(
            ChoiceModal(title, [(email, email) for email in promoters])
        )
        return picked[0] if picked else None

    @work
    async def action_add(self):
        name = await self.app.push_screen_wait(
            PromptModal("New label", placeholder="Label name")
        )
        if not name:
            return
        email = await self._pick_promoter(f"Owner for {name!r}")
        if not email:
            return
        try:
            row = await self.run_db(labels.create_label, name, email)
            self.notify(f"Created label #{row.id} {row.name!r} (/{row.slug}/).")
        except AdminError as exc:
            self.show_error(exc)
        self.action_refresh()

    @work
    async def action_rename(self):
        row = self._selected_row()
        if row is None:
            return
        name = await self.app.push_screen_wait(
            PromptModal(f"Rename label #{row.id}", value=row.name)
        )
        if not name or name == row.name:
            return
        try:
            updated = await self.run_db(labels.rename_label, row.id, name)
            self.notify(
                f"Renamed to {updated.name!r}; new public URL /{updated.slug}/ "
                "(the old one is gone)."
            )
        except AdminError as exc:
            self.show_error(exc)
        self.action_refresh()

    @work
    async def action_edit_description(self):
        row = self._selected_row()
        if row is None:
            return
        description = await self.app.push_screen_wait(
            PromptModal(
                f"Description for {row.name!r} (blank to clear)",
                value=row.description,
            )
        )
        if description is None:
            return
        try:
            await self.run_db(labels.set_description, row.id, description)
            self.notify(f"Description updated for {row.name!r}.")
        except AdminError as exc:
            self.show_error(exc)
        self.action_refresh()

    @work
    async def action_reassign(self):
        row = self._selected_row()
        if row is None:
            return
        email = await self._pick_promoter(
            f"New owner for {row.name!r} (currently {row.promoter})"
        )
        if not email or email == row.promoter:
            return
        try:
            await self.run_db(labels.reassign_label, row.id, email)
            self.notify(f"{row.name!r} reassigned to {email}.")
        except AdminError as exc:
            self.show_error(exc)
        self.action_refresh()

    @work
    async def action_delete(self):
        row = self._selected_row()
        if row is None:
            return
        detach = (
            f"Its {row.n_events} event(s) stay on the calendar without a label.\n"
            if row.n_events
            else ""
        )
        confirmed = await self.app.push_screen_wait(
            ConfirmModal(
                f"Delete label #{row.id} {row.name!r} ({row.promoter})?\n"
                f"{detach}This cannot be undone.",
                yes_label="Delete",
            )
        )
        if not confirmed:
            return
        try:
            name, n_events = await self.run_db(labels.delete_label, row.id)
            self.notify(f"Deleted {name!r}; {n_events} event(s) detached.")
        except AdminError as exc:
            self.show_error(exc)
        self.action_refresh()


# ── events ────────────────────────────────────────────────────────────────────


class EventsScreen(AdminScreen):
    BINDINGS = AdminScreen.BINDINGS + [
        Binding("u", "toggle_past", "Upcoming/all"),
        Binding("s", "set_status", "Status"),
        Binding("d", "delete", "Delete"),
        Binding("r", "refresh", "Refresh"),
    ]

    include_past = False

    def compose(self) -> ComposeResult:
        yield Header()
        yield DataTable(cursor_type="row")
        yield Footer()

    def on_mount(self):
        table = self.query_one(DataTable)
        table.add_columns("ID", "DATE", "STATUS", "NAME", "VENUE", "SUBMITTER")
        self.action_refresh()

    def _selected_id(self):
        table = self.query_one(DataTable)
        if table.row_count == 0:
            return None
        key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
        return int(key.value)

    @work(exclusive=True)
    async def action_refresh(self):
        self.sub_title = (
            "Events — all (newest first)" if self.include_past else "Events — upcoming"
        )
        rows = await self.run_db(events.list_events, self.include_past, 200)
        table = self.query_one(DataTable)
        table.clear()
        for row in rows:
            table.add_row(
                str(row.id),
                row.date,
                row.status,
                row.name[:40],
                row.venue[:25],
                row.submitter,
                key=str(row.id),
            )

    def action_toggle_past(self):
        self.include_past = not self.include_past
        self.action_refresh()

    @work
    async def action_set_status(self):
        event_id = self._selected_id()
        if event_id is None:
            return
        picked = await self.app.push_screen_wait(
            ChoiceModal(
                f"New status for event #{event_id}",
                [(status, status) for status in events.EVENT_STATUSES],
            )
        )
        if picked is None:
            return
        status, _ = picked
        try:
            row = await self.run_db(events.set_status, event_id, status)
            self.notify(f"Status set to {status} for #{row.id} {row.name!r}.")
        except AdminError as exc:
            self.show_error(exc)
        self.action_refresh()

    @work
    async def action_delete(self):
        event_id = self._selected_id()
        if event_id is None:
            return
        try:
            row = await self.run_db(events.get_event, event_id)
        except AdminError as exc:
            self.show_error(exc)
            return
        confirmed = await self.app.push_screen_wait(
            ConfirmModal(
                f"Delete event #{row.id} {row.name!r} ({row.date} @ {row.venue})?\n"
                "This cannot be undone.",
                yes_label="Delete",
            )
        )
        if not confirmed:
            return
        try:
            await self.run_db(events.delete_event, event_id)
            self.notify(f"Removed event #{event_id}.")
        except AdminError as exc:
            self.show_error(exc)
        self.action_refresh()


# ── dedup screens ─────────────────────────────────────────────────────────────


class DedupScreen(AdminScreen):
    """Shared shell: a button row on top, results in a RichLog below."""

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical():
            yield Horizontal(*self.controls(), classes="controls")
            yield RichLog(wrap=True, markup=False, id="log")
        yield Footer()

    def controls(self):
        raise NotImplementedError

    def log_line(self, text=""):
        self.query_one("#log", RichLog).write(text)

    def busy(self, value):
        for button in self.query(Button):
            button.disabled = value


class VenueDedupScreen(DedupScreen):
    plan = None

    def controls(self):
        yield Input(placeholder="Alternate DB path (blank = app DB)", id="db-path")
        yield Button("Scan", id="scan", variant="primary")
        yield Button("Apply auto merges", id="auto", disabled=True)
        yield Button("Review pairs", id="pairs", disabled=True)

    def on_mount(self):
        self.sub_title = "Venue dedup"
        self._ctx = None  # alternate-DB context, built on demand

    async def _run(self, fn, *args):
        """Like run_db, but against the alternate DB if a path is set."""
        path = self.query_one("#db-path", Input).value.strip()
        if not path:
            self._ctx = None
            return await self.run_db(fn, *args)
        if self._ctx is None or self._ctx[0] != path:
            app, _db, _submitter = await asyncio.to_thread(load_app, path)
            self._ctx = (path, app)
        flask_app = self._ctx[1]

        def inner():
            with flask_app.app_context():
                return fn(*args)

        return await asyncio.to_thread(inner)

    @work(exclusive=True)
    async def action_scan(self):
        self.busy(True)
        try:
            self.plan = await self._run(venues.scan)
        except AdminError as exc:
            self.show_error(exc)
            self.busy(False)
            return
        plan = self.plan
        self.log_line(f"Database: {plan.db_name} · {plan.n_venues} venues")
        self.log_line()
        for group in plan.auto_groups:
            self.log_line(f"[auto] keep {group.survivor.summary}")
            for loser in group.losers:
                self.log_line(f"       drop {loser.summary}")
        for pair in plan.pairs:
            self.log_line(f"[{pair.reason}]")
            self.log_line(f"    {pair.a.summary}")
            self.log_line(f"    {pair.b.summary}")
        self.log_line(
            f"{len(plan.auto_groups)} auto group(s), "
            f"{len(plan.pairs)} pair(s) needing confirmation."
        )
        self.log_line()
        self.busy(False)
        self.query_one("#auto", Button).disabled = not plan.auto_groups
        self.query_one("#pairs", Button).disabled = not plan.pairs

    @work
    async def action_auto(self):
        confirmed = await self.app.push_screen_wait(
            ConfirmModal("Apply all auto merges?", yes_label="Merge")
        )
        if not confirmed:
            return
        self.busy(True)
        merges = repointed = 0
        # A merge can backfill a missing city and reveal a new exact match,
        # so rescan and repeat until no auto groups remain.
        try:
            while True:
                plan = await self._run(venues.scan)
                if not plan.auto_groups:
                    break
                for group in plan.auto_groups:
                    result = await self._run(
                        venues.merge_venue_group,
                        group.survivor.id,
                        [loser.id for loser in group.losers],
                    )
                    merges += 1
                    repointed += result.events_repointed
                    self.log_line(f"merged into {group.survivor.summary}")
                    for warning in result.warnings:
                        self.log_line(f"  WARNING: {warning}")
        except AdminError as exc:
            self.show_error(exc)
        self.log_line(f"Done: {merges} merge(s), {repointed} event(s) repointed.")
        self.log_line()
        self.busy(False)
        self.plan = None
        self.action_scan()

    @work
    async def action_pairs(self):
        if not self.plan:
            return
        merged = declined = 0
        for pair in self.plan.pairs:
            confirmed = await self.app.push_screen_wait(
                ConfirmModal(
                    f"[{pair.reason}]\n\n{pair.a.summary}\n{pair.b.summary}\n\n"
                    f"Merge into #{pair.survivor_id}?",
                    yes_label="Merge",
                )
            )
            if not confirmed:
                declined += 1
                continue
            try:
                result = await self._run(
                    venues.merge_venue_group, pair.survivor_id, pair.loser_ids
                )
                merged += 1
                self.log_line(
                    f"merged pair into #{pair.survivor_id}, "
                    f"{result.events_repointed} event(s) repointed"
                )
                for warning in result.warnings:
                    self.log_line(f"  WARNING: {warning}")
            except AdminError as exc:
                self.log_line(f"  skipped: {exc}")
        self.log_line(f"Done: {merged} merge(s), {declined} pair(s) declined.")
        self.log_line()
        self.plan = None
        self.action_scan()

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "scan":
            self.action_scan()
        elif event.button.id == "auto":
            self.action_auto()
        elif event.button.id == "pairs":
            self.action_pairs()


class EventDedupScreen(DedupScreen):
    pairs = None

    def controls(self):
        yield Checkbox("Include past events", id="include-past")
        yield Button("Scan", id="scan", variant="primary")
        yield Button("Review pairs", id="review", disabled=True)

    def on_mount(self):
        self.sub_title = "Event dedup"

    @work(exclusive=True)
    async def action_scan(self):
        self.busy(True)
        include_past = self.query_one("#include-past", Checkbox).value
        n_scanned, self.pairs = await self.run_db(events.scan_event_dups, include_past)
        self.log_line(f"{n_scanned} event(s) scanned.")
        if not self.pairs:
            self.log_line("No candidate duplicates found.")
        for pair in self.pairs:
            self.log_line(f"[shared words: {', '.join(pair.shared)}]")
            for row in (pair.a, pair.b):
                self.log_line(
                    f"    #{row.id} {row.date} {row.status} {row.name} @ {row.venue}"
                )
        self.log_line()
        self.busy(False)
        self.query_one("#review", Button).disabled = not self.pairs

    @work
    async def action_review(self):
        if not self.pairs:
            return
        merged = declined = 0
        for pair in self.pairs:
            confirmed = await self.app.push_screen_wait(
                ConfirmModal(
                    f"[shared words: {', '.join(pair.shared)}]\n\n"
                    f"#{pair.a.id} {pair.a.date} {pair.a.name} @ {pair.a.venue}\n"
                    f"#{pair.b.id} {pair.b.date} {pair.b.name} @ {pair.b.venue}\n\n"
                    "Merge this pair (the richer event survives)?",
                    yes_label="Merge",
                )
            )
            if not confirmed:
                declined += 1
                continue
            result = await self.run_db(events.merge_event_pair, pair.a.id, pair.b.id)
            if result is None:
                self.log_line("  pair already merged away — skipped")
                continue
            survivor, n_deleted = result
            merged += 1
            self.log_line(
                f"merged into #{survivor.id} {survivor.name!r}, "
                f"{n_deleted} event(s) removed"
            )
        self.log_line(f"Done: {merged} merge(s), {declined} pair(s) declined.")
        self.log_line()
        self.pairs = None
        self.query_one("#review", Button).disabled = True

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "scan":
            self.action_scan()
        elif event.button.id == "review":
            self.action_review()


class GenreDedupScreen(DedupScreen):
    plan = None

    def controls(self):
        yield Button("Scan", id="scan", variant="primary")
        yield Button("Apply", id="apply", disabled=True)

    def on_mount(self):
        self.sub_title = "Genre dedup"

    @work(exclusive=True)
    async def action_scan(self):
        self.busy(True)
        self.plan = await self.run_db(events.scan_genre_dups)
        plan = self.plan
        self.log_line(f"Database: {plan.db_name} · {plan.n_events} event(s) scanned.")
        if not plan.groups:
            self.log_line("No duplicate genre spellings found.")
        else:
            self.log_line(f"{len(plan.groups)} genre(s) with inconsistent spelling:")
            for variants, canonical in plan.groups:
                self.log_line(f"    {variants} -> {canonical!r}")
        self.log_line()
        self.busy(False)
        self.query_one("#apply", Button).disabled = not plan.mapping

    @work
    async def action_apply(self):
        if not self.plan or not self.plan.mapping:
            return
        confirmed = await self.app.push_screen_wait(
            ConfirmModal(
                f"Normalize {len(self.plan.mapping)} genre spelling(s)?",
                yes_label="Apply",
            )
        )
        if not confirmed:
            return
        self.busy(True)
        changed = await self.run_db(events.apply_genre_merge, self.plan.mapping)
        self.log_line(f"Done: {changed} event(s) updated.")
        self.log_line()
        self.busy(False)
        self.plan = None
        self.query_one("#apply", Button).disabled = True

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "scan":
            self.action_scan()
        elif event.button.id == "apply":
            self.action_apply()


class QueueDupScreen(DedupScreen):
    """Triage staged events the dedup flagged as possible duplicates.

    These are hidden from the web approval queue. Per row: Discard (a real
    duplicate — drop it) or Unflag (false positive — send it back to the web
    queue for the normal approve/edit flow).
    """

    rows = None

    def controls(self):
        yield Button("Scan", id="scan", variant="primary")
        yield Button("Review", id="review", disabled=True)

    def on_mount(self):
        self.sub_title = "Queue duplicates"

    @work(exclusive=True)
    async def action_scan(self):
        self.busy(True)
        self.rows = await self.run_db(queue_review.list_flagged)
        if not self.rows:
            self.log_line("No flagged possible-duplicates in the queue.")
        else:
            self.log_line(f"{len(self.rows)} flagged possible-duplicate(s):")
            for row in self.rows:
                self.log_line(
                    f"    #{row.id} [{row.source}] {row.date} "
                    f"{row.title} @ {row.venue}/{row.city}"
                )
                if row.review_reason:
                    self.log_line(f"        collides with -> {row.review_reason}")
        self.log_line()
        self.busy(False)
        self.query_one("#review", Button).disabled = not self.rows

    @work
    async def action_review(self):
        if not self.rows:
            return
        discarded = unflagged = skipped = 0
        for row in self.rows:
            picked = await self.app.push_screen_wait(
                ChoiceModal(
                    f"#{row.id} [{row.source}] {row.date}\n"
                    f"{row.title} @ {row.venue}/{row.city}\n"
                    f"collides with: {row.review_reason or '(none recorded)'}",
                    [
                        ("skip", "Skip"),
                        ("discard", "Discard (real duplicate)"),
                        ("unflag", "Unflag (back to web queue)"),
                    ],
                )
            )
            if picked is None or picked[0] == "skip":
                skipped += 1
                continue
            action = picked[0]
            try:
                if action == "discard":
                    await self.run_db(queue_review.discard, row.id)
                    discarded += 1
                    self.log_line(f"discarded #{row.id} {row.title!r}")
                else:
                    await self.run_db(queue_review.unflag, row.id)
                    unflagged += 1
                    self.log_line(f"unflagged #{row.id} {row.title!r}")
            except AdminError as exc:
                self.show_error(exc)
        self.log_line(
            f"Done: {discarded} discarded, {unflagged} unflagged, {skipped} skipped."
        )
        self.log_line()
        self.rows = None
        self.query_one("#review", Button).disabled = True

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "scan":
            self.action_scan()
        elif event.button.id == "review":
            self.action_review()


# ── stats ─────────────────────────────────────────────────────────────────────


class StatsScreen(AdminScreen):
    BINDINGS = AdminScreen.BINDINGS + [
        Binding("t", "cycle_timeframe", "Timeframe"),
        Binding("r", "refresh", "Refresh"),
    ]

    timeframe = "all"

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical():
            yield Static("", id="overview")
            yield DataTable(cursor_type="row", id="leaderboard")
        yield Footer()

    def on_mount(self):
        self.sub_title = "Stats"
        table = self.query_one("#leaderboard", DataTable)
        table.add_columns("#", "EVENTS", "SUBMITTER")
        self.action_refresh()

    def action_cycle_timeframe(self):
        keys = list(stats.TIMEFRAMES)
        self.timeframe = keys[(keys.index(self.timeframe) + 1) % len(keys)]
        self.action_refresh()

    @work(exclusive=True)
    async def action_refresh(self):
        try:
            over = await self.run_db(stats.overview)
            board = await self.run_db(stats.leaderboard, self.timeframe)
        except AdminError as exc:
            self.show_error(exc)
            return
        size = f" ({over.db_size})" if over.db_size else ""
        past = over.total_events - over.upcoming_events
        self.query_one("#overview", Static).update(
            f"Database: {over.db_name}{size}\n"
            f"Events:       {over.total_events} "
            f"({over.upcoming_events} upcoming, {past} past)\n"
            f"Venues:       {over.n_venues}\n"
            f"Users:        {over.n_users} ({over.n_admins} admin(s), "
            f"{over.n_no_password} without password)\n"
            f"Scrape queue: {over.scrape_queue} pending\n"
            f"Skipped URLs: {over.skipped_urls}\n\n"
            f"Contributions ({board.timeframe_label}) — press t to change:"
        )
        table = self.query_one("#leaderboard", DataTable)
        table.clear()
        for i, (email, count) in enumerate(board.rows, 1):
            table.add_row(str(i), str(count), email)
        if board.unattributed:
            table.add_row("", str(board.unattributed), "(no submitter, e.g. scraped)")
        table.add_row("", str(board.total), "total")


# ── traffic ───────────────────────────────────────────────────────────────────


class TrafficScreen(AdminScreen):
    """Access-log traffic analysis: per-IP bot inference + event view trends.

    Three views share one table: `v` cycles Users (per-IP behavioural
    classification), Events (view counts over time) and Errors (paths ranked
    by 5xx responses). Selecting a row opens the IP's full profile in the
    Users view and the event's view history in the Events view.
    """

    VIEWS = ("users", "events", "errors")

    BINDINGS = AdminScreen.BINDINGS + [
        Binding("t", "cycle_timeframe", "Timeframe"),
        Binding("v", "toggle_view", "Users/Events/Errors"),
        Binding("r", "refresh", "Refresh"),
    ]

    timeframe = "7d"
    view = "users"

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical():
            yield Static("", id="traffic-summary")
            yield DataTable(cursor_type="row", id="traffic-table")
        yield Footer()

    def on_mount(self):
        self._profiles = {}
        self._event_rows = {}
        self.action_refresh()

    def action_cycle_timeframe(self):
        keys = list(traffic.TIMEFRAMES)
        self.timeframe = keys[(keys.index(self.timeframe) + 1) % len(keys)]
        self.action_refresh()

    def action_toggle_view(self):
        self.view = self.VIEWS[(self.VIEWS.index(self.view) + 1) % len(self.VIEWS)]
        self.action_refresh()

    @work(exclusive=True)
    async def action_refresh(self):
        label = traffic.TIMEFRAMES[self.timeframe]
        self.sub_title = f"Traffic — {self.view} · {label}"
        table = self.query_one("#traffic-table", DataTable)
        table.clear(columns=True)
        self._profiles = {}
        self._event_rows = {}
        try:
            if self.view == "users":
                await self._show_users()
            elif self.view == "events":
                await self._show_events()
            else:
                await self._show_errors()
        except AdminError as exc:
            self.query_one("#traffic-summary", Static).update(str(exc))
            self.show_error(exc)

    async def _show_users(self):
        report = await self.run_db(traffic.analyze, self.timeframe)
        buckets = " · ".join(
            f"{report.bucket_counts.get(b, 0)} {b}"
            for b in ("human", "crawler", "bot", "suspicious", "unclear")
        )
        statuses = " · ".join(f"{k} {v}" for k, v in report.status_counts.items())
        trend = "\n".join(
            f"  {d.date}  {d.requests:>6} req  {d.unique_ips:>5} ips  "
            f"~{d.est_humans} human"
            for d in report.days[-10:]
        )
        self.query_one("#traffic-summary", Static).update(
            f"Traffic ({report.timeframe_label}): {report.total_requests} "
            f"requests from {report.total_ips} IPs\n"
            f"Buckets: {buckets}\nStatus:  {statuses}\n{trend}\n\n"
            f"Worst IPs first — enter for detail, "
            f"v for events, t for timeframe:"
        )
        table = self.query_one("#traffic-table", DataTable)
        table.add_columns("IP", "BUCKET", "SCORE", "REQS", "LIVE", "SIGNALS", "UA")
        for p in report.ips:
            self._profiles[p.ip] = p
            table.add_row(
                p.ip,
                p.bucket,
                f"{p.score:g}",
                str(p.requests),
                "✓" if p.suspect else "",
                ", ".join(s.split(" ")[0] for s in p.signals) or "—",
                p.ua_sample[:60],
                key=p.ip,
            )

    async def _show_events(self):
        result = await self.run_db(traffic.events, self.timeframe)
        self.query_one("#traffic-summary", Static).update(
            f"Event views ({result.timeframe_label}), from recorded daily "
            f"counts — trend spans the last {result.spark_days} day(s).\n"
            f"Most viewed first — enter for detail, v for view, t for timeframe:"
        )
        table = self.query_one("#traffic-table", DataTable)
        table.add_columns("HITS", "VISITORS", "TREND", "DATE", "EVENT")
        for row in result.rows:
            self._event_rows[str(row.event_id)] = row
            table.add_row(
                str(row.hits),
                str(row.visitors),
                row.sparkline,
                row.date_label,
                row.name,
                key=str(row.event_id),
            )

    async def _show_errors(self):
        report = await self.run_db(traffic.server_errors, self.timeframe)
        share = (
            f" ({report.total_5xx / report.total_requests:.2%})"
            if report.total_5xx
            else ""
        )
        statuses = " · ".join(f"{k} {v}" for k, v in report.statuses.items())
        verdict = (
            f"By status: {statuses}\nMost 5xx first"
            if report.rows
            else "No server errors in this window"
        )
        self.query_one("#traffic-summary", Static).update(
            f"Server errors ({report.timeframe_label}): {report.total_5xx} 5xx "
            f"in {report.total_requests} requests{share}\n"
            f"{verdict} — v for view, t for timeframe:"
        )
        table = self.query_one("#traffic-table", DataTable)
        table.add_columns("5XX", "STATUSES", "IPS", "LAST SEEN", "PATH")
        for row in report.rows:
            table.add_row(
                str(row.count),
                ", ".join(f"{k}×{v}" for k, v in row.statuses.items()),
                str(row.unique_ips),
                row.last_seen,
                row.path,
            )

    def _show_event_detail(self, row):
        days = "\n".join(
            f"  {day}  {hits:>5} hits  {visitors:>5} visitors"
            for day, hits, visitors in row.recent_days
        )
        if row.date_label:
            info = (
                f"Date:      {row.date_label} ({row.status})\n"
                f"Venue:     {row.venue or '-'}\n"
                f"Genre:     {row.genre or '-'}\n"
                f"Submitter: {row.submitter or '-'}\n"
            )
        else:
            info = "The event itself has been deleted; only its view counts remain.\n"
        self.app.push_screen(
            MessageModal(
                f"#{row.event_id} — {row.name}",
                f"{info}"
                f"Views:     {row.hits} hits, {row.visitors} visitors "
                f"in the window\n"
                f"By day, newest first:\n{days or '  none'}",
            )
        )

    def on_data_table_row_selected(self, event: DataTable.RowSelected):
        event_row = self._event_rows.get(event.row_key.value)
        if event_row is not None:
            self._show_event_detail(event_row)
            return
        profile = self._profiles.get(event.row_key.value)
        if profile is None:
            return
        paths = "\n".join(f"  {n:>5}  {p}" for p, n in profile.top_paths)
        signals = "\n".join(f"  {s}" for s in profile.signals) or "  none"
        live = (
            f"flagged live (score {profile.suspect_score:g}: "
            f"{', '.join(profile.suspect_signals) or 'no signals recorded'})"
            if profile.suspect
            else "not flagged by the live detector"
        )
        self.app.push_screen(
            MessageModal(
                f"{profile.ip} — {profile.bucket} (score {profile.score:g})",
                f"Requests:  {profile.requests} "
                f"({profile.first_seen} → {profile.last_seen})\n"
                f"UAs:       {profile.n_uas} distinct, e.g. {profile.ua_sample}\n"
                f"Live:      {live}\n"
                f"Signals:\n{signals}\n"
                f"Top paths:\n{paths}",
            )
        )


# ── database ──────────────────────────────────────────────────────────────────


class DatabaseScreen(AdminScreen):
    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical():
            yield Static("", id="db-info")
            with Horizontal(classes="controls"):
                yield Input(
                    placeholder="Backup destination (blank = backups/…)",
                    id="dest",
                )
                yield Button("Backup", id="backup", variant="primary")
                yield Button("Vacuum", id="vacuum")
        yield Footer()

    def on_mount(self):
        self.sub_title = "Database"
        self.refresh_info()

    @work(exclusive=True)
    async def refresh_info(self):
        over = await self.run_db(stats.overview)
        size = f" ({over.db_size})" if over.db_size else ""
        self.query_one("#db-info", Static).update(f"Database: {over.db_name}{size}")

    def busy(self, value):
        for button in self.query(Button):
            button.disabled = value

    @work
    async def on_button_pressed(self, event: Button.Pressed):
        self.busy(True)
        try:
            if event.button.id == "backup":
                dest = self.query_one("#dest", Input).value.strip() or None
                path, size = await self.run_db(db_tools.backup, dest)
                self.notify(f"Backed up to {path} ({fmt_size(size)}).", timeout=8)
            elif event.button.id == "vacuum":
                name, before, after = await self.run_db(db_tools.vacuum)
                self.notify(
                    f"Vacuumed {name}: {fmt_size(before)} -> {fmt_size(after)}."
                )
        except AdminError as exc:
            self.show_error(exc)
        self.busy(False)
        self.refresh_info()


# ── logs ──────────────────────────────────────────────────────────────────────


class LogsScreen(AdminScreen):
    BINDINGS = AdminScreen.BINDINGS + [
        Binding("e", "toggle_error", "Access/error"),
        Binding("f", "toggle_follow", "Follow"),
        Binding("r", "reload", "Reload"),
    ]

    show_error_log = False
    following = False

    def compose(self) -> ComposeResult:
        yield Header()
        yield RichLog(wrap=False, markup=False, max_lines=2000, id="log")
        yield Footer()

    def on_mount(self):
        self._offset = 0
        self._timer = None
        self.action_reload()

    @property
    def log_path(self):
        return ERROR_LOG if self.show_error_log else ACCESS_LOG

    def _update_subtitle(self):
        name = "error log" if self.show_error_log else "access log"
        follow = " · following" if self.following else ""
        self.sub_title = f"Logs — {name}{follow}"

    def action_reload(self):
        self._update_subtitle()
        widget = self.query_one("#log", RichLog)
        widget.clear()
        path = self.log_path
        if not path.exists():
            widget.write(f"No log file at {path}")
            self._offset = 0
            return
        with open(path, "rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - 131072))
            text = handle.read().decode("utf-8", "replace")
        for line in text.splitlines()[-400:]:
            widget.write(line)
        self._offset = size

    def action_toggle_error(self):
        self.show_error_log = not self.show_error_log
        self.action_reload()

    def action_toggle_follow(self):
        self.following = not self.following
        self._update_subtitle()
        if self.following:
            self._timer = self.set_interval(1.0, self._poll)
        elif self._timer is not None:
            self._timer.stop()
            self._timer = None

    def _poll(self):
        path = self.log_path
        if not path.exists():
            return
        size = path.stat().st_size
        if size < self._offset:  # rotated/truncated — start over
            self.action_reload()
            return
        if size == self._offset:
            return
        widget = self.query_one("#log", RichLog)
        with open(path, "rb") as handle:
            handle.seek(self._offset)
            text = handle.read().decode("utf-8", "replace")
        for line in text.splitlines():
            widget.write(line)
        self._offset = size
