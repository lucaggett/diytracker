"""Modal dialogs: confirmations, prompts, choice pickers, messages."""

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Checkbox,
    Input,
    Label,
    RadioButton,
    RadioSet,
    Static,
)


class ConfirmModal(ModalScreen[bool]):
    """Yes/No question; defaults to No, escape cancels."""

    BINDINGS = [Binding("escape", "cancel", "Cancel", show=False)]

    def __init__(self, message, yes_label="Yes"):
        super().__init__()
        self.message = message
        self.yes_label = yes_label

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-box"):
            yield Static(self.message, classes="modal-message")
            with Horizontal(classes="modal-buttons"):
                yield Button("No", id="no", variant="primary")
                yield Button(self.yes_label, id="yes", variant="error")

    def on_mount(self):
        self.query_one("#no", Button).focus()

    def on_button_pressed(self, event: Button.Pressed):
        self.dismiss(event.button.id == "yes")

    def action_cancel(self):
        self.dismiss(False)


class PromptModal(ModalScreen[str | None]):
    """Single text input; returns the value, or None on cancel."""

    BINDINGS = [Binding("escape", "cancel", "Cancel", show=False)]

    def __init__(self, title, placeholder="", value="", password=False):
        super().__init__()
        self.title_text = title
        self.placeholder = placeholder
        self.value = value
        self.password = password

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-box"):
            yield Label(self.title_text, classes="modal-title")
            yield Input(
                value=self.value,
                placeholder=self.placeholder,
                password=self.password,
                id="prompt-input",
            )
            with Horizontal(classes="modal-buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("OK", id="ok", variant="primary")

    def on_mount(self):
        self.query_one("#prompt-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted):
        self.dismiss(event.value.strip())

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "ok":
            self.dismiss(self.query_one("#prompt-input", Input).value.strip())
        else:
            self.dismiss(None)

    def action_cancel(self):
        self.dismiss(None)


class PasswordModal(ModalScreen[str | None]):
    """Password entry with confirmation; returns it, or None on cancel."""

    BINDINGS = [Binding("escape", "cancel", "Cancel", show=False)]

    def __init__(self, title="Set password"):
        super().__init__()
        self.title_text = title

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-box"):
            yield Label(self.title_text, classes="modal-title")
            yield Input(placeholder="New password", password=True, id="pw1")
            yield Input(placeholder="Repeat password", password=True, id="pw2")
            yield Static("", id="pw-error", classes="modal-error")
            with Horizontal(classes="modal-buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("OK", id="ok", variant="primary")

    def on_mount(self):
        self.query_one("#pw1", Input).focus()

    def _submit(self):
        pw1 = self.query_one("#pw1", Input).value
        pw2 = self.query_one("#pw2", Input).value
        if not pw1:
            self.query_one("#pw-error", Static).update("Password is empty.")
            return
        if pw1 != pw2:
            self.query_one("#pw-error", Static).update("Passwords do not match.")
            return
        self.dismiss(pw1)

    def on_input_submitted(self, event: Input.Submitted):
        if event.input.id == "pw1":
            self.query_one("#pw2", Input).focus()
        else:
            self._submit()

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "ok":
            self._submit()
        else:
            self.dismiss(None)

    def action_cancel(self):
        self.dismiss(None)


class ChoiceModal(ModalScreen["tuple | None"]):
    """Radio-button pick from (value, label) choices, with an optional extra
    checkbox. Returns (value, checkbox_checked), or None on cancel."""

    BINDINGS = [Binding("escape", "cancel", "Cancel", show=False)]

    def __init__(self, title, choices, default=None, checkbox_label=None):
        super().__init__()
        self.title_text = title
        self.choices = list(choices)
        self.default = default if default is not None else self.choices[0][0]
        self.checkbox_label = checkbox_label

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-box"):
            yield Label(self.title_text, classes="modal-title")
            with RadioSet(id="choice-set"):
                for value, label in self.choices:
                    yield RadioButton(label, value=value == self.default)
            if self.checkbox_label:
                yield Checkbox(self.checkbox_label, id="choice-extra")
            with Horizontal(classes="modal-buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("OK", id="ok", variant="primary")

    def on_mount(self):
        self.query_one("#choice-set", RadioSet).focus()

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id != "ok":
            self.dismiss(None)
            return
        radio_set = self.query_one("#choice-set", RadioSet)
        index = radio_set.pressed_index
        if index < 0:
            index = 0
        extra = False
        if self.checkbox_label:
            extra = self.query_one("#choice-extra", Checkbox).value
        self.dismiss((self.choices[index][0], extra))

    def action_cancel(self):
        self.dismiss(None)


class FormModal(ModalScreen["dict | None"]):
    """Several labelled inputs at once; returns {key: value}, or None on
    cancel. Editing a venue one PromptModal per field would mean six dialogs
    to fix a PLZ and a city, with no way to see the record while typing."""

    BINDINGS = [Binding("escape", "cancel", "Cancel", show=False)]

    def __init__(self, title, fields):
        """*fields* is [(key, label, value)], rendered in order."""
        super().__init__()
        self.title_text = title
        self.fields = list(fields)

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-box"):
            yield Label(self.title_text, classes="modal-title")
            for key, label, value in self.fields:
                yield Label(label, classes="modal-field-label")
                yield Input(value=value or "", id=f"field-{key}")
            with Horizontal(classes="modal-buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Save", id="ok", variant="primary")

    def on_mount(self):
        if self.fields:
            self.query_one(f"#field-{self.fields[0][0]}", Input).focus()

    def _values(self):
        return {
            key: self.query_one(f"#field-{key}", Input).value.strip()
            for key, _label, _value in self.fields
        }

    def on_input_submitted(self, event: Input.Submitted):
        self.dismiss(self._values())

    def on_button_pressed(self, event: Button.Pressed):
        self.dismiss(self._values() if event.button.id == "ok" else None)

    def action_cancel(self):
        self.dismiss(None)


class MessageModal(ModalScreen[None]):
    """Static text (e.g. an invite link to copy) with an OK button."""

    BINDINGS = [Binding("escape", "close", "Close", show=False)]

    def __init__(self, title, message):
        super().__init__()
        self.title_text = title
        self.message = message

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-box"):
            yield Label(self.title_text, classes="modal-title")
            yield Static(self.message, classes="modal-message")
            with Horizontal(classes="modal-buttons"):
                yield Button("OK", id="ok", variant="primary")

    def on_button_pressed(self, event: Button.Pressed):
        self.dismiss(None)

    def action_close(self):
        self.dismiss(None)
