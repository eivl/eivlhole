"""Root Typer application."""

import typer

from eivlhole import __version__

app = typer.Typer(
    name="eivlhole",
    help="Wormhole-style file transfer over SSH, with scheduled unattended delivery.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"eivlhole {__version__}")
        raise typer.Exit()


@app.callback()
def root(
    version: bool = typer.Option(
        False, "--version", callback=_version_callback, is_eager=True, help="Show version."
    ),
) -> None:
    """Wormhole-style file transfer over SSH."""


def main() -> None:
    app()
