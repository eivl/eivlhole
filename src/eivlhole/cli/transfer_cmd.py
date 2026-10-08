"""eivlhole send / receive."""

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated

import typer
from rich.markup import escape
from rich.progress import BarColumn, DownloadColumn, Progress, TextColumn, TransferSpeedColumn
from rich.prompt import Confirm
from rich.table import Table

from eivlhole import transfer
from eivlhole.cli.common import State, console, get_state, handle_errors, run_async
from eivlhole.errors import EivlholeError
from eivlhole.mailbox.base import Manifest, ProgressFn
from eivlhole.mailbox.sftp import SftpMailbox
from eivlhole.ssh.client import sftp, target_from_host


def format_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{n} B"


@contextmanager
def transfer_progress() -> Iterator[ProgressFn]:
    progress = Progress(
        TextColumn("{task.description}", markup=False),
        BarColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        console=console,
        transient=True,
    )
    tasks: dict[str, int] = {}
    started = False

    def cb(name: str, done: int, total: int) -> None:
        nonlocal started
        if not started:
            progress.start()
            started = True
        if name not in tasks:
            tasks[name] = progress.add_task(name, total=total)
        progress.update(tasks[name], completed=done, total=total)

    try:
        yield cb
    finally:
        if started:
            progress.stop()


def _print_manifest(manifest: Manifest) -> None:
    table = Table("file", "size", title=f"from {escape(manifest.sender)}")
    for entry in manifest.files:
        table.add_row(escape(entry.path), format_size(entry.size))
    console.print(table)
    console.print(f"{len(manifest.files)} file(s), {format_size(manifest.total_size)} total")


@handle_errors
def send(
    ctx: typer.Context,
    paths_: Annotated[
        list[Path], typer.Argument(metavar="PATH...", help="Files or directories.")
    ],
    to: str | None = typer.Option(None, "--to", help="Drop host name; default host if omitted."),
    code_length: int | None = typer.Option(None, "--code-length", min=1, max=8),
    wait: bool = typer.Option(True, "--wait/--no-wait", help="Wait until the receiver is done."),
    poll: float = typer.Option(2.0, "--poll", hidden=True),
) -> None:
    """Upload files to the drop host and print a code for the receiver."""
    state: State = get_state(ctx)
    cfg = state.config
    host = cfg.host_for(to)
    target = target_from_host(host)
    words = code_length or cfg.defaults.code_words

    sent: dict[str, transfer.SendResult] = {}

    async def go() -> None:
        async with sftp(
            target, known_hosts=state.known_hosts(), prompt_trust=state.trust_prompt()
        ) as s:
            mailbox = SftpMailbox(s, cfg.defaults.drop_root)
            with state.history() as hist, transfer_progress() as cb:
                result = await transfer.send(
                    paths_, mailbox, history=hist, host_name=host.name, words=words, progress=cb
                )
            sent["result"] = result
            total = sum(e.size for e in result.files)
            console.print(f"Sent {len(result.files)} file(s), {format_size(total)}.")
            console.print(f"Code: [bold green]{result.code}[/]")
            console.print(f"On the other side run: eivlhole receive {result.code}")
            if not wait:
                return
            console.print(
                "[dim]Waiting for the receiver (Ctrl-C leaves the transfer on the drop host)[/]"
            )
            await transfer.wait_for_receipt(
                mailbox,
                result.transfer_id,
                poll=poll,
                on_state=lambda st: console.print(f"[dim]{escape(str(st))}[/]"),
            )
            console.print("[green]Done.[/]")

    try:
        run_async(go())
    except KeyboardInterrupt as exc:
        if "result" in sent:
            console.print(
                f"Transfer left on the drop host; code {sent['result'].code} "
                "stays valid until swept."
            )
        else:
            console.print(
                "Interrupted before the transfer was published; "
                "nothing was left on the drop host."
            )
        raise typer.Exit(130) from exc


@handle_errors
def receive(
    ctx: typer.Context,
    code: str | None = typer.Argument(None, help="Code from the sender; prompted if omitted."),
    out: Annotated[
        Path | None, typer.Option("--out", "-o", help="Destination directory.")
    ] = None,
    yes: bool = typer.Option(False, "--yes", "-y", help="Accept without confirmation."),
    overwrite: bool = typer.Option(False, "--overwrite", help="Replace existing local files."),
    from_host: str | None = typer.Option(
        None, "--from", help="Drop host name; default if omitted."
    ),
) -> None:
    """Fetch the files behind a code from the drop host."""
    state: State = get_state(ctx)
    cfg = state.config
    if code is None:
        if state.unattended:
            raise EivlholeError("a code is required in unattended mode")
        code = typer.prompt("Code")
    host = cfg.host_for(from_host)
    target = target_from_host(host)
    out_dir = out or Path(cfg.defaults.receive_dir)

    async def confirm(manifest: Manifest) -> bool:
        _print_manifest(manifest)
        if yes or state.unattended:
            return True
        return await asyncio.to_thread(
            Confirm.ask, f"Receive into {escape(str(out_dir.resolve()))}?", default=True
        )

    async def go() -> transfer.ReceiveResult:
        async with sftp(
            target, known_hosts=state.known_hosts(), prompt_trust=state.trust_prompt()
        ) as s:
            mailbox = SftpMailbox(s, cfg.defaults.drop_root)
            with state.history() as hist, transfer_progress() as cb:
                return await transfer.receive(
                    code,
                    mailbox,
                    out_dir,
                    history=hist,
                    host_name=host.name,
                    confirm=confirm,
                    overwrite=overwrite,
                    progress=cb,
                )

    result = run_async(go())
    for p in result.files:
        console.print(f"received {p}", markup=False, highlight=False)
    console.print(
        f"[green]{len(result.files)} file(s) received into {escape(str(out_dir.resolve()))}[/]"
    )
