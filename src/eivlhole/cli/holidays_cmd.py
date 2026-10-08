"""eivlhole holidays: check, list."""

from datetime import date

import typer
from rich.markup import escape
from rich.table import Table

from eivlhole.cli.common import console, get_state, handle_errors
from eivlhole.cli.run_cmd import parse_date
from eivlhole.config import HolidayConfig
from eivlhole.errors import EivlholeError
from eivlhole.jobs import calendar

holidays_app = typer.Typer(
    help="Inspect the holiday calendar used to skip jobs.", no_args_is_help=True
)


def _calendar_for(ctx: typer.Context, job: str | None) -> HolidayConfig:
    if job is None:
        return HolidayConfig()
    cfg = get_state(ctx).config
    if job not in cfg.jobs:
        raise EivlholeError(f"unknown job '{job}'")
    return cfg.jobs[job].holidays


@holidays_app.command("check")
@handle_errors
def check(
    ctx: typer.Context,
    day: str = typer.Argument("today", help="YYYY-MM-DD, today or yesterday."),
    job: str | None = typer.Option(None, "--job", help="Use this job's holiday settings."),
) -> None:
    """Say whether a job would run on a date."""
    when = parse_date(day)
    decision = calendar.decide(when, _calendar_for(ctx, job))
    if decision.skip:
        console.print(f"{when}: skip ({escape(decision.reason or '')})")
    else:
        console.print(f"{when}: run")


@holidays_app.command("list")
@handle_errors
def list_(
    ctx: typer.Context,
    year: int = typer.Option(date.today().year, "--year"),
    job: str | None = typer.Option(None, "--job"),
) -> None:
    """List the skipped dates in a year (weekends excluded)."""
    table = Table("date", "weekday", "reason")
    for day, reason in calendar.skip_days(year, _calendar_for(ctx, job)):
        table.add_row(day.isoformat(), day.strftime("%A"), escape(reason))
    console.print(table)
