"""The eivlhole terminal UI. Layout like lazygit; all work goes through the async core."""

from __future__ import annotations

import asyncio
import logging
import os
import shlex
import sqlite3
import subprocess
from pathlib import Path

from rich.markup import escape
from rich.text import Text
from textual import work
from textual.app import App, ComposeResult, SuspendNotSupported
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Footer, Header, ProgressBar, RichLog, Static

from eivlhole import paths, transfer
from eivlhole.cli.config_cmd import _editor_command as editor_command
from eivlhole.cli.transfer_cmd import format_size
from eivlhole.config import Config, load_config
from eivlhole.errors import EivlholeError
from eivlhole.history import FileRecord, History
from eivlhole.jobs.runner import run_job
from eivlhole.mailbox.base import Manifest
from eivlhole.mailbox.sftp import SftpMailbox
from eivlhole.ssh import keys
from eivlhole.ssh.client import sftp, target_from_host
from eivlhole.tui import data
from eivlhole.tui.panels import HistoryPanel, HostsPanel, JobsPanel
from eivlhole.tui.screens import ConfirmScreen, HelpScreen, TextPrompt

log = logging.getLogger(__name__)

CONFIRM_FILES = 15  # file lines listed in the receive confirmation


def split_paths(text: str) -> list[Path]:
    """Split user-typed paths; keep backslashes on Windows, honour quotes on both."""
    windows = os.name == "nt"
    tokens = shlex.split(text, posix=not windows)
    if windows:
        tokens = [t[1:-1] if len(t) >= 2 and t[0] == t[-1] == '"' else t for t in tokens]
    return [Path(t).expanduser() for t in tokens]


class EivlholeApp(App[None]):
    CSS_PATH = "app.tcss"
    TITLE = "eivlhole"
    BINDINGS = [
        Binding("1", "focus_panel('jobs')", "Jobs"),
        Binding("2", "focus_panel('hosts')", "Hosts"),
        Binding("3", "focus_panel('history')", "History"),
        Binding("slash", "filter", "Filter"),
        Binding("r", "run_job", "Run job"),
        Binding("d", "dry_run", "Dry run"),
        Binding("s", "send", "Send"),
        Binding("R", "receive", "Receive"),
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
        self.poll: float = 2.0
        self.pending_codes: dict[str, str] = {}  # code -> what it carries, until received

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

    async def on_unmount(self) -> None:
        # Fallback for shutdowns that bypass action_quit: Textual has already
        # cancelled the workers; let them record their runs before closing.
        await self._settle(self._busy_workers())
        self._close_history()

    def _close_history(self) -> None:
        if self.history is not None:
            self.history.close()
            self.history = None

    # --- quit -----------------------------------------------------------

    def _busy_workers(self) -> list:
        return [w for w in self.workers if w.is_running and w.group != "quit"]

    async def action_quit(self) -> None:
        busy = self._busy_workers()
        if not busy:
            self._quit_worker()
            return
        groups = sorted({w.group for w in busy})
        lines = [f"running: {', '.join(groups)}"]
        if self.pending_codes:
            lines.append("")
            lines.append("codes not yet received (they stop being watched here):")
            lines.extend(f"  {code}  {what}" for code, what in self.pending_codes.items())
        self.push_screen(
            ConfirmScreen("Operations are still running. Quit anyway?", "\n".join(lines)),
            callback=self._confirm_quit,
        )

    def _confirm_quit(self, ok: bool | None) -> None:
        if ok:
            self._quit_worker()

    async def _settle(self, workers: list) -> None:
        """Wait up to 10s for cancelled workers to finish their cleanup."""
        if not workers:
            return
        # Worker.wait raises WorkerCancelled for each cancelled worker; collect, not raise.
        waits = asyncio.gather(*(w.wait() for w in workers), return_exceptions=True)
        try:
            await asyncio.wait_for(waits, 10)
        except TimeoutError:
            log.warning("workers did not stop within 10s; quitting anyway")

    @work(exclusive=True, group="quit")
    async def _quit_worker(self) -> None:
        busy = self._busy_workers()
        if busy:
            self.set_status("stopping running operations...")
            for w in busy:
                w.cancel()
            await self._settle(busy)
        self._close_history()
        self.exit()

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
        panel.border_title = escape(f"3 History ({self.filter.describe()})")

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

    # --- progress -----------------------------------------------------

    def _progress(self, name: str, done: int, total: int) -> None:
        bar = self.query_one("#progress", ProgressBar)
        bar.update(total=max(total, 1), progress=done)
        self.set_status(f"{name}: {format_size(done)} / {format_size(total)}")

    def _reset_progress(self) -> None:
        self.query_one("#progress", ProgressBar).update(total=100, progress=0)

    def _report_failure(self, what: str, exc: BaseException) -> None:
        text = str(exc) or type(exc).__name__
        self.set_status(f"{what}: {text}", error=True)
        self.write_log(f"{what}: {text}")

    # --- jobs ---------------------------------------------------------

    def action_run_job(self) -> None:
        self._start_job(dry_run=False)

    def action_dry_run(self) -> None:
        self._start_job(dry_run=True)

    def _start_job(self, *, dry_run: bool) -> None:
        name = self.selected_job()
        if not name:
            self.set_status("no job selected; add one with `eivlhole jobs add`", error=True)
            return
        if any(w.group == "job" and w.is_running for w in self.workers):
            self.set_status("a job is already running; wait for it to finish", error=True)
            return
        self.run_job_worker(name, dry_run)

    @work(group="job")
    async def run_job_worker(self, name: str, dry_run: bool) -> None:
        self._reset_progress()
        self.set_status(f"running {name}{' (dry run)' if dry_run else ''}...")
        try:
            result = await run_job(
                name,
                self.config,
                history=self.history,
                dry_run=dry_run,
                known_hosts=paths.known_hosts_file(),
                progress=self._progress,
            )
            for w in result.warnings:
                self.write_log(f"warning: {w}")
            if result.status == "skipped":
                self.set_status(f"{name} skipped: {result.reason}")
                self.write_log(f"{name}: skipped ({result.reason})")
            elif result.status == "dry-run":
                pending = [o for o in result.outcomes if o.status == "pending"]
                self.write_log(f"{name}: would send {len(pending)} file(s)")
                for o in result.outcomes:
                    self.write_log(
                        f"  {o.local} -> {o.remote}" + (f"  ({o.error})" if o.error else "")
                    )
                self.set_status(f"{name}: dry run, {len(pending)} file(s)")
            else:
                for o in result.outcomes:
                    self.write_log(
                        f"  {o.status:6} {o.local.name}" + (f"  ({o.error})" if o.error else "")
                    )
                counts = f"{len(result.sent)} sent, {len(result.failed)} failed"
                self.set_status(
                    f"{name}: {result.status}, {counts}",
                    error=result.status != "ok",
                )
        except EivlholeError as exc:
            self._report_failure(f"run {name}", exc)
        except Exception as exc:  # noqa: BLE001 - a worker must never crash the app
            log.exception("run_job failed")
            self._report_failure(f"run {name}", exc)
        finally:
            if self.is_running:
                self.populate_history()

    # --- send ---------------------------------------------------------

    def action_send(self) -> None:
        self.push_screen(
            TextPrompt(
                "Send: file or directory paths (quote paths with spaces)",
                placeholder="C:/reports/summary.xlsx",
            ),
            callback=self._start_send,
        )

    def _start_send(self, text: str | None) -> None:
        if not text:
            return
        self.send_worker(text)

    @work(group="send")
    async def send_worker(self, text: str) -> None:
        self._reset_progress()
        code: str | None = None
        try:
            local_paths = split_paths(text)
            transfer.collect_files(local_paths)
            host = self.config.host_for(None)
            target = target_from_host(host)
            self.set_status(f"sending to {host.name}...")
            async with sftp(target, known_hosts=paths.known_hosts_file(), prompt_trust=None) as s:
                mailbox = SftpMailbox(s, self.config.defaults.drop_root)
                result = await transfer.send(
                    local_paths,
                    mailbox,
                    history=self.history,
                    host_name=host.name,
                    words=self.config.defaults.code_words,
                    progress=self._progress,
                )
                self.write_log(f"Code: {result.code}  ({len(result.files)} file(s))")
                self.write_log(f"On the other side: eivlhole receive {result.code}")
                self.pending_codes[result.code] = f"{len(result.files)} file(s)"
                code = result.code
                self.set_status(f"sent; code {result.code}; waiting for the receiver")
                self.populate_history()
                await transfer.wait_for_receipt(
                    mailbox,
                    result.transfer_id,
                    poll=self.poll,
                    on_state=lambda st: self.write_log(f"{result.code}: {st}"),
                )
                self.set_status(f"{result.code} received")
        except EivlholeError as exc:
            self._report_failure("send", exc)
        except Exception as exc:  # noqa: BLE001
            log.exception("send failed")
            self._report_failure("send", exc)
        finally:
            if code is not None:
                self.pending_codes.pop(code, None)
            if self.is_running:
                self.populate_history()

    # --- receive ------------------------------------------------------

    def action_receive(self) -> None:
        self.push_screen(
            TextPrompt("Receive: code from the sender", placeholder="7-tunnel-stairway-glucose"),
            callback=self._start_receive,
        )

    def _start_receive(self, text: str | None) -> None:
        if not text:
            return
        self.receive_worker(text)

    @work(group="receive")
    async def receive_worker(self, code: str) -> None:
        self._reset_progress()
        out_dir = Path(self.config.defaults.receive_dir).expanduser()

        async def confirm(manifest: Manifest) -> bool:
            total = format_size(manifest.total_size)
            lines = [f"{len(manifest.files)} file(s), {total} from {manifest.sender}", ""]
            shown = manifest.files[:CONFIRM_FILES]
            lines += [f"{e.path}  {format_size(e.size)}" for e in shown]
            if len(manifest.files) > len(shown):
                lines.append(f"... and {len(manifest.files) - len(shown)} more")
            body = "\n".join(lines)
            return await self.push_screen_wait(
                ConfirmScreen(f"Receive into {out_dir.resolve()}?", body)
            )

        try:
            host = self.config.host_for(None)
            target = target_from_host(host)
            self.set_status(f"receiving {code}...")
            async with sftp(target, known_hosts=paths.known_hosts_file(), prompt_trust=None) as s:
                mailbox = SftpMailbox(s, self.config.defaults.drop_root)
                result = await transfer.receive(
                    code,
                    mailbox,
                    out_dir,
                    history=self.history,
                    host_name=host.name,
                    confirm=confirm,
                    progress=self._progress,
                )
            for p in result.files:
                self.write_log(f"received {p}")
            self.set_status(f"{len(result.files)} file(s) received into {out_dir.resolve()}")
        except EivlholeError as exc:
            self._report_failure("receive", exc)
        except Exception as exc:  # noqa: BLE001
            log.exception("receive failed")
            self._report_failure("receive", exc)
        finally:
            if self.is_running:
                self.populate_history()
