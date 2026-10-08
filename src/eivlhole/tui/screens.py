"""Modal screens: help (this task), text prompt and confirm (Task 3)."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Input, Label, Static

HELP_TEXT = """\
eivlhole keys

  1 / 2 / 3     focus Jobs / Hosts / History
  Tab           next panel        j / k / arrows  move
  Enter         show details

  r             run selected job          d   dry run
  s             send files (asks path)    R   receive (asks code)
  t             test selected host        e   edit config in $EDITOR
  /             filter history: name or glob, a date, or start..end
  g             refresh config/history    c   cancel running operations
  ?             this help                 q   quit (asks if busy)

Host trust is never prompted here: run `eivlhole hosts trust NAME` in a shell.
"""


class HelpScreen(ModalScreen[None]):
    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("q", "close", "Close", show=False),
        Binding("question_mark", "close", "Close", show=False),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="help"):
            yield Static(HELP_TEXT, markup=False)

    def action_close(self) -> None:
        self.dismiss(None)


class TextPrompt(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, title: str, *, placeholder: str = "", value: str = "") -> None:
        super().__init__()
        self.title_text = title
        self.placeholder = placeholder
        self.initial = value

    def compose(self) -> ComposeResult:
        with Vertical(id="prompt"):
            yield Static(self.title_text, markup=False)
            yield Input(value=self.initial, placeholder=self.placeholder, id="value")
            yield Label("Enter = ok   Esc = cancel")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip())

    def action_cancel(self) -> None:
        self.dismiss(None)


class ConfirmScreen(ModalScreen[bool]):
    BINDINGS = [
        Binding("y", "yes", "Yes"),
        Binding("n", "no", "No"),
        Binding("escape", "no", "Cancel", show=False),
    ]

    def __init__(self, title: str, body: str) -> None:
        super().__init__()
        self.title_text = title
        self.body = body

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm"):
            yield Static(self.title_text, markup=False)
            with VerticalScroll(id="confirm-body"):
                yield Static(self.body, id="confirm-text", markup=False)
            yield Label("y = yes   n = no")

    def action_yes(self) -> None:
        self.dismiss(True)

    def action_no(self) -> None:
        self.dismiss(False)
