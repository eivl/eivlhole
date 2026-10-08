"""eivlhole schedule: install, uninstall, print, status."""

import typer
from rich.markup import escape

from eivlhole import paths
from eivlhole.cli.common import console, err, get_state, handle_errors
from eivlhole.errors import EivlholeError
from eivlhole.schedule.base import Scheduler, ScheduleSpec, build_spec, get_scheduler

schedule_app = typer.Typer(
    help="Register a job with Task Scheduler (Windows) or cron (Linux).", no_args_is_help=True
)

_PLATFORM = typer.Option(None, "--platform", hidden=True, help="windows | linux")


def _spec(ctx: typer.Context, job: str) -> ScheduleSpec:
    cfg = get_state(ctx).config
    if job not in cfg.jobs:
        raise EivlholeError(f"unknown job '{job}'")
    return build_spec(cfg.jobs[job], paths.config_dir())


def _scheduler(platform: str | None) -> Scheduler:
    if platform not in (None, "windows", "linux"):
        raise EivlholeError("--platform must be windows or linux")
    return get_scheduler(platform)


@schedule_app.command("print")
@handle_errors
def print_(ctx: typer.Context, job: str, platform: str | None = _PLATFORM) -> None:
    """Show the task definition and how to register it by hand."""
    spec = _spec(ctx, job)
    sched = _scheduler(platform)
    console.print(sched.render(spec), markup=False, highlight=False, soft_wrap=True)
    console.print(sched.manual(spec), markup=False, highlight=False, soft_wrap=True)


@schedule_app.command("install")
@handle_errors
def install(ctx: typer.Context, job: str, platform: str | None = _PLATFORM) -> None:
    """Register the job. On failure, prints the manual command and exits 1."""
    spec = _spec(ctx, job)
    sched = _scheduler(platform)
    result = sched.install(spec)
    if result.ok:
        days = ", ".join(spec.days)
        console.print(f"installed {escape(job)} with {sched.name}: {spec.time} on {days}")
        return
    err.print(f"[red]could not register with {sched.name}:[/] {escape(result.output)}")
    console.print(result.manual, markup=False, highlight=False, soft_wrap=True)
    raise typer.Exit(1)


@schedule_app.command("uninstall")
@handle_errors
def uninstall(ctx: typer.Context, job: str, platform: str | None = _PLATFORM) -> None:
    """Remove the job's task or crontab line."""
    get_state(ctx)
    sched = _scheduler(platform)
    result = sched.uninstall(job)
    if result.ok:
        console.print(f"removed {escape(job)} from {sched.name}")
        return
    err.print(f"[red]could not remove from {sched.name}:[/] {escape(result.output)}")
    console.print(result.manual, markup=False, highlight=False, soft_wrap=True)
    raise typer.Exit(1)


@schedule_app.command("status")
@handle_errors
def status(ctx: typer.Context, job: str, platform: str | None = _PLATFORM) -> None:
    """Show whether the job is registered."""
    get_state(ctx)
    sched = _scheduler(platform)
    found = sched.status(job)
    if found is None:
        console.print(f"{escape(job)}: not installed in {sched.name}")
    else:
        console.print(found, markup=False, highlight=False, soft_wrap=True)
