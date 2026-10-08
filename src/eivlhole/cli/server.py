"""eivlhole server: init, sweep, status (run on the drop host)."""

from pathlib import Path

import typer

from eivlhole.cli.common import console, get_state, handle_errors
from eivlhole.server import layout
from eivlhole.server import sweep as sweep_mod

server_app = typer.Typer(help="Drop host administration (run on the server).", no_args_is_help=True)
_ROOT = typer.Option(Path("/srv/eivlhole"), "--root", help="Drop root directory.")


@server_app.command("init")
@handle_errors
def init(
    ctx: typer.Context,
    root: Path = _ROOT,
    user: str = typer.Option("eivlhole", "--user", help="Service account name."),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Create codes/, transfers/ and home/ with the right modes, and print the sshd snippet."""
    get_state(ctx)
    result = layout.init_layout(root, user=user, dry_run=dry_run)
    for p in result.created:
        console.print(f"{'would create' if dry_run else 'created'} {p}", markup=False)
    if not result.created:
        console.print("layout already present")
    for w in result.warnings:
        console.print(f"[yellow]warning:[/] {w}")
    console.print(layout.sshd_snippet(root, user), markup=False, highlight=False)


@server_app.command("sweep")
@handle_errors
def sweep(
    ctx: typer.Context,
    root: Path = _ROOT,
    max_age: str = typer.Option("7d", "--max-age", help="Remove transfers older than this."),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Delete expired transfers and dangling codes."""
    get_state(ctx)
    result = sweep_mod.sweep(root, sweep_mod.parse_duration(max_age), dry_run=dry_run)
    verb = "would remove" if dry_run else "removed"
    console.print(
        f"{verb} {len(result.removed_transfers)} transfer(s) and "
        f"{len(result.removed_codes)} code(s); kept {result.kept_transfers} transfer(s)"
    )
    for t in result.removed_transfers:
        console.print(f"  transfer {t}", markup=False)
    for c in result.removed_codes:
        console.print(f"  code {c}", markup=False)


@server_app.command("status")
@handle_errors
def status(ctx: typer.Context, root: Path = _ROOT) -> None:
    """Show counts of live transfers and codes."""
    get_state(ctx)
    st = sweep_mod.status(root)
    console.print(f"transfers: {st['transfers']} (claimed: {st['claimed']})")
    console.print(f"codes: {st['codes']}")
    oldest = st["oldest_hours"]
    console.print(f"oldest transfer: {'none' if oldest is None else f'{oldest:.1f} h'}")
