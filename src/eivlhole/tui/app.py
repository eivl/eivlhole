"""The eivlhole terminal UI. Layout like lazygit; all work goes through the async core."""

from __future__ import annotations

import logging
import sqlite3
import subprocess
from pathlib import Path

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult, SuspendNotSupported
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Footer, Header, ProgressBar, RichLog, Static

from eivlhole import paths
from eivlhole.cli.config_cmd import _editor_command as editor_command
from eivlhole.config import Config, load_config
from eivlhole.errors import EivlholeError
from eivlhole.history import FileRecord, History
from eivlhole.ssh import keys
from eivlhole.ssh.client import target_from_host
from eivlhole.tui import data
from eivlhole.tui.panels import HistoryPanel, HostsPanel, JobsPanel
from eivlhole.tui.screens import HelpScreen, TextPrompt

log = logging.getLogger(__name__)


class EivlholeApp(App[None]):
    CSS_PATH = "app.tcss"
    TITLE = "eivlhole"
    BINDINGS = [
        Binding("1", "focus_panel('jobs')", "Jobs"),
        Binding("2", "focus_panel('hosts')", "Hosts"),
        Binding("3", "focus_panel('history')", "History"),
        Binding("slash", "filter", "Filter"),
        Binding("e", "edit_config", "Edit config"),
        Binding("t", "test_host", "Test host"),
        Binding("question_mark", "help", "Help"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.config = Config()
        self.history: History | None = None
        self.filter = data.HistoryFilter()
        self.filter_text = ""
        self.config_error: str | None = None
        self.file_records: dict[int, FileRecord] = {}

    # --- layout -------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="body"):
            with Vertical(id="left"):
                yield JobsPanel(id="jobs")
                yield HostsPanel(id="hosts")
                yield HistoryPanel(id="history")
            with Vertical(id="main"):
                yield Static("", id="detail", expand=True)
                yield RichLog(id="log", wrap=True, markup=False, highlight=False)
                yield ProgressBar(id="progress", total=100, show_eta=False)
        yield Static("", id="status")
        yield Footer()

    def on_mount(self) -> None:
        self.reload()
        self.query_one(JobsPanel).focus()
        self.show_detail("jobs", self.query_one(JobsPanel).selected_key())

    def on_unmount(self) -> None:
        if self.history is not None:
            self.history.close()
            self.history = None

    # --- data ---------------------------------------------------------

    def reload(self) -> None:
        config_failed = False
        self.config_error = None
        try:
            self.config = load_config(paths.config_file())
        except EivlholeError as exc:
            config_failed = True
            self.config_error = str(exc)
            self.config = Config()
            self.set_status(str(exc), error=True)
        if self.history is None:
            try:
                self.history = History(paths.history_file())
            except (sqlite3.Error, OSError) as exc:
                log.warning("history unavailable: %s", exc)
                self.set_status(f"history unavailable: {exc}", error=True)
        jobs = data.job_rows(self.config)
        self.query_one(JobsPanel).replace_rows(
            [(j.name, j.host, j.days, j.time, j.sources) for j in jobs], [j.name for j in jobs]
        )
        hosts = data.host_rows(self.config, paths.known_hosts_file())
        self.query_one(HostsPanel).replace_rows(
            [
                (h.name, f"{h.hostname}:{h.port}", h.user, "yes" if h.trusted else "no")
                for h in hosts
            ],
            [h.name for h in hosts],
        )
        self.populate_history()
        if not config_failed and not self.config.hosts and not self.config.jobs:
            self.set_status(
                "no hosts or jobs configured; run `eivlhole hosts add` / `jobs add`, or press e"
            )
        widget = self.focused
        if isinstance(widget, DataTable) and widget.id:
            self.show_detail(widget.id, widget.selected_key())

    def populate_history(self) -> None:
        panel = self.query_one(HistoryPanel)
        if self.history is None:
            panel.replace_rows([], [])
            return
        try:
            self._fill_history(panel, self.history)
        except Exception as exc:
            log.exception("history query failed")
            self.set_status(f"history unavailable: {exc}", error=True)
            panel.replace_rows([], [])

    def _fill_history(self, panel: HistoryPanel, history: History) -> None:
        if self.filter.active:
            panel.set_mode("files")
            recs = data.file_rows(history, self.filter)
            self.file_records = {r.id: r for r in recs}
            panel.replace_rows(
                [(data.fmt_local(r.ts), r.direction, r.filename, r.status) for r in recs],
                [f"file:{r.id}" for r in recs],
            )
        else:
            panel.set_mode("runs")
            runs = data.run_rows(history)
            panel.replace_rows(
                [
                    (str(r.id), data.fmt_local(r.started), r.kind, r.job or "", r.status)
                    for r in runs
                ],
                [f"run:{r.id}" for r in runs],
            )
        panel.border_title = f"3 History ({self.filter.describe()})"

    # --- output helpers -----------------------------------------------

    def set_status(self, text: str, *, error: bool = False) -> None:
        widget = self.query_one("#status", Static)
        widget.update(Text(text.replace("\n", " "), style="bold red" if error else ""))

    def write_log(self, text: str) -> None:
        self.query_one("#log", RichLog).write(text)

    def show_detail(self, panel_id: str, key: str | None) -> None:
        detail = self.query_one("#detail", Static)
        if key is None:
            detail.update(Text("nothing selected"))
            return
        try:
            if panel_id == "jobs":
                text = data.job_detail(self.config, key)
            elif panel_id == "hosts":
                text = data.host_detail(self.config, key, paths.known_hosts_file())
            elif key.startswith("run:") and self.history is not None:
                text = data.run_detail(self.history, int(key[4:]))
            elif key.startswith("file:"):
                rec = self.file_records.get(int(key[5:]))
                text = data.file_detail(rec) if rec else "record not loaded"
            else:
                text = ""
        except Exception as exc:
            log.exception("detail failed")
            text = f"cannot show details: {exc}"
        detail.update(Text(text))

    # --- events and actions -------------------------------------------

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.row_key is not None and event.data_table.id:
            self.show_detail(event.data_table.id, str(event.row_key.value))

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id:
            self.show_detail(event.data_table.id, str(event.row_key.value))

    def on_descendant_focus(self, event) -> None:
        widget = event.widget
        if isinstance(widget, DataTable) and widget.id:
            self.show_detail(widget.id, widget.selected_key())

    def action_focus_panel(self, name: str) -> None:
        self.query_one(f"#{name}").focus()

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    def selected_job(self) -> str | None:
        return self.query_one(JobsPanel).selected_key()

    def selected_host(self) -> str | None:
        return self.query_one(HostsPanel).selected_key() or self.config.defaults.host

    # --- filter -------------------------------------------------------

    def action_filter(self) -> None:
        self.push_screen(
            TextPrompt(
                "Filter history: filename or glob, a date, or start..end (empty = all runs)",
                placeholder="daily-*.csv 2026-10-07",
                value=self.filter_text,
            ),
            callback=self._apply_filter,
        )

    def _apply_filter(self, text: str | None) -> None:
        if text is None:
            return
        try:
            flt = data.parse_history_filter(text)
        except EivlholeError as exc:
            self.set_status(str(exc), error=True)
            return
        self.filter, self.filter_text = flt, text
        self.populate_history()
        self.set_status(f"history: {flt.describe()}")

    # --- edit config --------------------------------------------------

    def _run_editor(self, cmd: list[str], path: Path) -> int:
        try:
            with self.suspend():
                return subprocess.run([*cmd, str(path)]).returncode
        except SuspendNotSupported:
            return subprocess.run([*cmd, str(path)]).returncode

    def action_edit_config(self) -> None:
        try:
            cmd = editor_command()
        except EivlholeError as exc:
            self.set_status(str(exc), error=True)
            return
        path = paths.config_file()
        try:
            if not path.exists():
                path.write_text("[defaults]\n\n[hosts]\n\n[jobs]\n", encoding="utf-8")
            code = self._run_editor(cmd, path)
        except OSError as exc:
            self.set_status(f"cannot edit config: {exc}", error=True)
            return
        self.reload()
        if self.config_error:
            self.set_status(f"config has problems: {self.config_error}", error=True)
        elif code:
            self.set_status(f"editor exited with {code}; config reloaded", error=True)
        else:
            self.set_status("config reloaded")

    # --- test host ----------------------------------------------------

    def action_test_host(self) -> None:
        name = self.selected_host()
        if not name or name not in self.config.hosts:
            self.set_status("no host selected; add one with `eivlhole hosts add`", error=True)
            return
        self.test_host_worker(name)

    @work(exclusive=True, group="host")
    async def test_host_worker(self, name: str) -> None:
        try:
            self.set_status(f"testing {name}...")
            target = target_from_host(self.config.hosts[name])
            cwd = await keys.test_connection(
                target, known_hosts=paths.known_hosts_file(), prompt_trust=None
            )
            self.set_status(f"OK: {name} (sftp cwd {cwd})")
            self.write_log(f"test {name}: OK")
            self.reload()
        except EivlholeError as exc:
            self.set_status(str(exc), error=True)
            self.write_log(f"test {name}: {exc}")
            return
        except Exception as exc:  # noqa: BLE001 - a worker must never crash the app
            log.exception("test host failed")
            self.set_status(f"test {name} failed: {exc}", error=True)
            return
