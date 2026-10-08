"""eivlhole config: path, edit."""

import os
import shlex
import shutil
import subprocess
import sys

import typer

from eivlhole import paths
from eivlhole.cli.common import console, err, get_state, handle_errors
from eivlhole.config import load_config
from eivlhole.errors import ConfigError

config_app = typer.Typer(help="Locate or edit config.toml.", no_args_is_help=True)


def _editor_command() -> list[str]:
    """$VISUAL or $EDITOR, else notepad on Windows, else the first of nano/vi found."""
    env = os.environ.get("VISUAL") or os.environ.get("EDITOR")
    if env:
        return shlex.split(env, posix=sys.platform != "win32")
    if sys.platform == "win32":
        return ["notepad"]
    return [shutil.which("nano") or shutil.which("vi") or "vi"]


@config_app.command("path")
@handle_errors
def path(ctx: typer.Context) -> None:
    """Print the config file path."""
    get_state(ctx)
    console.print(str(paths.config_file()), markup=False, highlight=False)


@config_app.command("edit")
@handle_errors
def edit(ctx: typer.Context) -> None:
    """Open config.toml in $EDITOR (notepad on Windows), then validate it."""
    get_state(ctx)
    cfg_path = paths.config_file()
    if not cfg_path.exists():
        cfg_path.write_text("[defaults]\n\n[hosts]\n\n[jobs]\n", encoding="utf-8")
    subprocess.run([*_editor_command(), str(cfg_path)], check=False)
    try:
        load_config(cfg_path)
    except ConfigError as exc:
        err.print(f"[yellow]config has problems:[/]\n{exc}")
        raise typer.Exit(1) from exc
    console.print("config OK")
