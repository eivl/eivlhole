"""Modal screens: help (this task), text prompt and confirm (Task 3)."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

HELP_TEXT = """\
eivlhole keys

  1 / 2 / 3     focus Jobs / Hosts / History
  Tab           next panel        j / k / arrows  move
  Enter         show details

  r             run selected job          d   dry run
  s             send files (asks path)    R   receive (asks code)
  t             test selected host        e   edit config in $EDITOR
  /             filter history: name or glob, a date, or start..end
  ?             this help                 q   quit

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
