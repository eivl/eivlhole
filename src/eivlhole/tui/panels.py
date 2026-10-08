"""The three left-hand DataTable panels."""

from __future__ import annotations

from textual.binding import Binding
from textual.widgets import DataTable


class Panel(DataTable):
    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
    ]
    TITLE = ""
    COLUMNS: tuple[str, ...] = ()

    def __init__(self, **kwargs) -> None:
        super().__init__(cursor_type="row", zebra_stripes=True, **kwargs)
        self.border_title = self.TITLE
        self.add_columns(*self.COLUMNS)  # before mount is fine; rows are added by the app

    def replace_rows(self, rows: list[tuple], keys: list[str]) -> None:
        current = self.cursor_row
        self.clear()
        for row, key in zip(rows, keys, strict=True):
            self.add_row(*row, key=key)
        if rows:
            self.move_cursor(row=min(current, len(rows) - 1))

    def selected_key(self) -> str | None:
        if self.row_count == 0:
            return None
        return str(self.coordinate_to_cell_key(self.cursor_coordinate).row_key.value)


class JobsPanel(Panel):
    TITLE = "1 Jobs"
    COLUMNS = ("job", "host", "days", "time", "sources")


class HostsPanel(Panel):
    TITLE = "2 Hosts"
    COLUMNS = ("host", "address", "user", "trusted")


class HistoryPanel(Panel):
    TITLE = "3 History"
    RUN_COLUMNS = ("id", "when", "kind", "job", "status")
    FILE_COLUMNS = ("when", "dir", "file", "status")
    COLUMNS = RUN_COLUMNS

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.mode = "runs"  # columns for this mode were added by Panel.__init__

    def set_mode(self, mode: str) -> None:
        if mode == self.mode:
            return
        self.mode = mode
        self.clear(columns=True)
        self.add_columns(*(self.RUN_COLUMNS if mode == "runs" else self.FILE_COLUMNS))
