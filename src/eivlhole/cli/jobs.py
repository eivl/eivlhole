"""eivlhole jobs: list, show, add, remove."""

import tomli_w
import typer
from rich.markup import escape
from rich.table import Table

from eivlhole.cli.common import console, get_state, handle_errors
from eivlhole.config import (
    JobConfig,
    PatternConfig,
    ScheduleConfig,
    parse_config,
    to_dict,
)
from eivlhole.errors import ConfigError, EivlholeError

jobs_app = typer.Typer(help="Manage scheduled delivery jobs.", no_args_is_help=True)


@jobs_app.command("list")
@handle_errors
def list_(ctx: typer.Context) -> None:
    """List configured jobs."""
    cfg = get_state(ctx).config
    table = Table("job", "host", "remote_dir", "days", "time", "files", "patterns", "on_success")
    for job in cfg.jobs.values():
        table.add_row(
            escape(job.name),
            escape(job.host or f"(default: {cfg.defaults.host})"),
            escape(job.remote_dir),
            ",".join(job.schedule.days) or "-",
            job.schedule.time,
            str(len(job.files)),
            str(len(job.patterns)),
            job.on_success,
        )
    console.print(table)


@jobs_app.command("show")
@handle_errors
def show(ctx: typer.Context, name: str) -> None:
    """Print one job as TOML."""
    cfg = get_state(ctx).config
    if name not in cfg.jobs:
        raise EivlholeError(f"unknown job '{name}'")
    console.print(
        tomli_w.dumps({"jobs": {name: to_dict(cfg)["jobs"][name]}}), markup=False, highlight=False
    )


def _parse_pattern(text: str) -> PatternConfig:
    directory, sep, regex = text.partition("::")
    if not sep or not directory or not regex:
        raise EivlholeError(f"--pattern must be DIR::REGEX, got {text!r}")
    return PatternConfig(dir=directory, regex=regex)


_REMOTE_DIR = typer.Option(..., "--remote-dir", help="Target directory on the host.")
_FILES = typer.Option([], "--file", help="Exact file path (repeatable).")
_PATTERNS = typer.Option([], "--pattern", help="DIR::REGEX (repeatable).")
_HOST = typer.Option(None, "--host", help="Host name; default host if omitted.")


@jobs_app.command("add")
@handle_errors
def add(
    ctx: typer.Context,
    name: str,
    remote_dir: str = _REMOTE_DIR,
    host: str | None = _HOST,
    files: list[str] = _FILES,
    patterns: list[str] = _PATTERNS,
    days: str = typer.Option("", "--days", help="Comma-separated weekdays, e.g. tue,thu."),
    time: str = typer.Option("07:00", "--time", help="HH:MM, 24-hour."),
    on_success: str = typer.Option("keep", "--on-success", help="keep | move | delete"),
    retries: int = typer.Option(3, "--retries"),
) -> None:
    """Add a job to config.toml (validated before saving)."""
    state = get_state(ctx)
    cfg = state.config
    if name in cfg.jobs:
        raise EivlholeError(f"job '{name}' already exists; remove it first")
    job = JobConfig(
        name=name,
        remote_dir=remote_dir,
        host=host,
        files=list(files),
        patterns=[_parse_pattern(p) for p in patterns],
        schedule=ScheduleConfig(time=time, days=[d.strip() for d in days.split(",") if d.strip()]),
        on_success=on_success,
        retries=retries,
    )
    cfg.jobs[name] = job
    try:
        parse_config(to_dict(cfg))
    except ConfigError:
        del cfg.jobs[name]
        raise
    state.save()
    console.print(f"added job {escape(name)}; next: eivlhole run {escape(name)} --dry-run")


@jobs_app.command("remove")
@handle_errors
def remove(ctx: typer.Context, name: str) -> None:
    """Remove a job."""
    state = get_state(ctx)
    if name not in state.config.jobs:
        raise EivlholeError(f"unknown job '{name}'")
    del state.config.jobs[name]
    state.save()
    console.print(f"removed job {escape(name)}")
