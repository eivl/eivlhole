"""Root Typer application."""

import os
from pathlib import Path
from typing import Annotated

import typer

from eivlhole import __version__, paths
from eivlhole.cli.common import State, setup_logging
from eivlhole.cli.config_cmd import config_app
from eivlhole.cli.hosts import hosts_app
from eivlhole.cli.keys import keys_app
from eivlhole.cli.server import server_app
from eivlhole.cli.transfer_cmd import receive, send

app = typer.Typer(
    name="eivlhole",
    help="Wormhole-style file transfer over SSH, with scheduled unattended delivery.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
app.add_typer(hosts_app, name="hosts")
app.add_typer(keys_app, name="keys")
app.add_typer(config_app, name="config")
app.add_typer(server_app, name="server")
app.command("send")(send)
app.command("receive")(receive)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"eivlhole {__version__}")
        raise typer.Exit()


@app.callback()
def root(
    ctx: typer.Context,
    version: bool = typer.Option(
        False, "--version", callback=_version_callback, is_eager=True, help="Show version."
    ),
    config_dir: Annotated[
        Path | None,
        typer.Option(
            "--config-dir",
            help=f"Override the config directory (also ${paths.ENV_CONFIG_DIR}).",
        ),
    ] = None,
    unattended: bool = typer.Option(
        False, "--unattended", help="Never prompt; fail instead. Used by scheduled runs."
    ),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Debug output on stderr."),
) -> None:
    """Wormhole-style file transfer over SSH."""
    if config_dir is not None:
        os.environ[paths.ENV_CONFIG_DIR] = str(config_dir)
    paths.ensure_dirs()
    setup_logging(verbose)
    ctx.obj = State(unattended=unattended, verbose=verbose)


def main() -> None:
    app()
