"""eivlhole history: files, runs, show."""

import json
from dataclasses import asdict
from datetime import datetime

import typer
from rich.markup import escape
from rich.table import Table

from eivlhole.cli.common import console, get_state, handle_errors
from eivlhole.cli.run_cmd import parse_date
from eivlhole.errors import EivlholeError
from eivlhole.history import FileRecord, History, RunRecord

history_app = typer.Typer(help="Query the transaction log.", no_args_is_help=True)


def _local(ts: str | None) -> str:
    if not ts:
        return ""
    return datetime.fromisoformat(ts).astimezone().strftime("%Y-%m-%d %H:%M")


def _history(ctx: typer.Context) -> History:
    state = get_state(ctx)
    cm = state.history()
    hist = cm.__enter__()
    if hist is None:
        cm.__exit__(None, None, None)
        raise EivlholeError("history unavailable (see the log for the reason)")
    ctx.call_on_close(lambda: cm.__exit__(None, None, None))
    return hist


def _emit_json(records: list) -> None:
    for rec in records:
        console.print(json.dumps(asdict(rec)), markup=False, highlight=False, soft_wrap=True)


def _files_table(records: list[FileRecord]) -> Table:
    table = Table("when", "run", "dir", "file", "status", "size", "sha256", "error")
    table.columns[3].no_wrap = True
    for f in records:
        table.add_row(
            _local(f.ts),
            str(f.run_id),
            f.direction,
            escape(f.filename),
            f.status,
            str(f.size or 0),
            (f.sha256 or "")[:12],
            escape(f.error or ""),
        )
    return table


NAMES_ARG = typer.Argument(
    None, metavar="[NAME]...", help="Exact filenames, or globs with * and ?."
)


@history_app.command("files")
@handle_errors
def files(
    ctx: typer.Context,
    names: list[str] = NAMES_ARG,
    on: str | None = typer.Option(None, "--on", help="One local calendar day."),
    since: str | None = typer.Option(None, "--since"),
    until: str | None = typer.Option(None, "--until"),
    job: str | None = typer.Option(None, "--job"),
    host: str | None = typer.Option(None, "--host"),
    status: str | None = typer.Option(None, "--status", help="sent | received | failed | skipped"),
    as_json: bool = typer.Option(False, "--json", help="One JSON object per line."),
    limit: int = typer.Option(50, "-n", "--limit"),
) -> None:
    """Was a file transferred, and when? Newest first."""
    hist = _history(ctx)
    records = hist.files(
        list(names or []),
        on=parse_date(on) if on else None,
        since=parse_date(since) if since else None,
        until=parse_date(until) if until else None,
        job=job,
        host=host,
        status=status,
        limit=limit,
    )
    if as_json:
        _emit_json(records)
        return
    if not records:
        console.print("no matching files")
        return
    console.print(_files_table(records))


@history_app.command("runs")
@handle_errors
def runs(
    ctx: typer.Context,
    job: str | None = typer.Option(None, "--job"),
    since: str | None = typer.Option(None, "--since"),
    until: str | None = typer.Option(None, "--until"),
    status: str | None = typer.Option(None, "--status", help="ok | partial | failed | skipped"),
    as_json: bool = typer.Option(False, "--json"),
    limit: int = typer.Option(50, "-n", "--limit"),
) -> None:
    """List runs (jobs, sends, receives). Newest first."""
    hist = _history(ctx)
    records: list[RunRecord] = hist.runs(
        job=job,
        since=parse_date(since) if since else None,
        until=parse_date(until) if until else None,
        status=status,
        limit=limit,
    )
    if as_json:
        _emit_json(records)
        return
    if not records:
        console.print("no runs recorded")
        return
    table = Table("id", "started", "kind", "job", "host", "status", "exit", "error")
    for r in records:
        table.add_row(
            str(r.id),
            _local(r.started),
            r.kind,
            escape(r.job or ""),
            escape(r.host or ""),
            r.status,
            str(r.exit_code if r.exit_code is not None else ""),
            escape(r.error or ""),
        )
    console.print(table)


@history_app.command("show")
@handle_errors
def show(
    ctx: typer.Context,
    run_id: int,
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Show one run and every file it touched."""
    hist = _history(ctx)
    run = hist.run(run_id)
    if run is None:
        raise EivlholeError(f"no run with id {run_id}")
    recs = hist.files_for_run(run_id)
    if as_json:
        console.print(
            json.dumps({"run": asdict(run), "files": [asdict(f) for f in recs]}),
            markup=False,
            highlight=False,
            soft_wrap=True,
        )
        return
    console.print(
        f"run {run.id}: {run.kind} {escape(run.job or '')} on {escape(run.host or '')} "
        f"started {_local(run.started)} finished {_local(run.finished)} status {run.status}"
        + (f" error: {escape(run.error)}" if run.error else "")
    )
    console.print(_files_table(recs))
