"""eivlhole run JOB: unattended delivery."""

import logging
from datetime import date, timedelta

import typer
from rich.markup import escape
from rich.table import Table

from eivlhole.cli.common import State, console, get_state, handle_errors, run_async
from eivlhole.cli.transfer_cmd import transfer_progress
from eivlhole.config import Config
from eivlhole.errors import EivlholeError
from eivlhole.jobs import runner


def parse_date(text: str) -> date:
    lowered = text.strip().lower()
    if lowered == "today":
        return date.today()
    if lowered == "yesterday":
        return date.today() - timedelta(days=1)
    try:
        return date.fromisoformat(lowered)
    except ValueError as exc:
        raise EivlholeError(f"invalid date {text!r}; use YYYY-MM-DD, today or yesterday") from exc


def record_failed_start(state: State, job: str, exc: EivlholeError) -> None:
    """Best-effort history row for a run that failed before run_job could write one."""
    try:
        with state.history() as hist:
            if hist is not None:
                rid = hist.start_run("job", job=job, host=None)
                hist.finish_run(rid, "failed", exit_code=exc.exit_code, error=str(exc))
    except Exception:  # noqa: BLE001 - history must never mask the original error
        logging.getLogger("eivlhole").exception("history write failed")


@handle_errors
def run(
    ctx: typer.Context,
    job: str = typer.Argument(..., help="Job name from config.toml."),
    force: bool = typer.Option(False, "--force", help="Ignore weekends and holidays."),
    dry_run: bool = typer.Option(False, "--dry-run", help="List what would be sent and exit."),
    date_: str | None = typer.Option(None, "--date", hidden=True),
) -> None:
    """Deliver a job's files to its host. Never prompts.

    Exit codes: 0 ok, 1 failed, 2 skipped, 3 partial.
    """
    state: State = get_state(ctx)

    async def go(cfg: Config, today: date | None) -> runner.RunResult:
        with state.history() as hist, transfer_progress() as cb:
            return await runner.run_job(
                job,
                cfg,
                history=hist,
                today=today,
                force=force,
                dry_run=dry_run,
                known_hosts=state.known_hosts(),
                progress=cb,
            )

    try:
        # run_job writes its own history row once it starts; anything raised before
        # that (bad config, unknown job, bad --date) still leaves a failed run behind.
        result = run_async(go(state.config, parse_date(date_) if date_ else None))
    except EivlholeError as exc:
        if not dry_run:
            record_failed_start(state, job, exc)
        raise
    for w in result.warnings:
        console.print(f"[yellow]warning:[/] {escape(w)}")
    if result.status == "skipped":
        console.print(f"skipped: {escape(result.reason or '')}")
        raise typer.Exit(result.exit_code)
    if result.status == "dry-run":
        console.print(
            f"would send {len([o for o in result.outcomes if o.status == 'pending'])} file(s):"
        )
        for o in result.outcomes:
            flag = "" if o.status == "pending" else f"  ({escape(o.error or o.status)})"
            console.print(f"  {escape(str(o.local))} -> {escape(o.remote)}{flag}")
        return
    table = Table("file", "status", "attempts", "error")
    for o in result.outcomes:
        table.add_row(escape(o.local.name), o.status, str(o.attempts), escape(o.error or ""))
    console.print(table)
    console.print(
        f"{result.status}: {len(result.sent)} sent, {len(result.failed)} failed"
        + (f" ({escape(result.reason)})" if result.reason and not result.sent else "")
    )
    if result.exit_code:
        raise typer.Exit(result.exit_code)
