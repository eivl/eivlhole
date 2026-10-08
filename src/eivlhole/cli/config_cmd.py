"""eivlhole config: path, edit."""

import os
import shlex
import shutil
import subprocess
from pathlib import Path

import typer
from rich.markup import escape

from eivlhole import paths
from eivlhole.cli.common import console, err, get_state, handle_errors
from eivlhole.config import load_config
from eivlhole.errors import ConfigError, EivlholeError

config_app = typer.Typer(help="Locate or edit config.toml.", no_args_is_help=True)


def _editor_command() -> list[str]:
    """$VISUAL, then $EDITOR; else notepad on Windows, else nano or vi."""
    windows = os.name == "nt"
    for var in ("VISUAL", "EDITOR"):
        value = os.environ.get(var, "").strip()
        if not value:
            continue
        if Path(value).is_file() or shutil.which(value):
            return [value]
        tokens = shlex.split(value, posix=not windows)
        if windows:
            tokens = [t[1:-1] if len(t) >= 2 and t[0] == t[-1] == '"' else t for t in tokens]
        if tokens:
            return tokens
    candidates = ["notepad"] if windows else ["nano", "vi"]
    for name in candidates:
        if shutil.which(name):
            return [name]
    raise EivlholeError("no editor found; set $EDITOR")


@config_app.command("path")
@handle_errors
def path(ctx: typer.Context) -> None:
    """Print the config file path."""
    get_state(ctx)
    console.print(str(paths.config_file()), markup=False, highlight=False, soft_wrap=True)


@config_app.command("edit")
@handle_errors
def edit(ctx: typer.Context) -> None:
    """Open config.toml in $EDITOR (notepad on Windows), then validate it."""
    state = get_state(ctx)
    if state.unattended:
        raise EivlholeError("config edit needs an interactive terminal")
    cfg_path = paths.config_file()
    if not cfg_path.exists():
        cfg_path.write_text("[defaults]\n\n[hosts]\n\n[jobs]\n", encoding="utf-8")
    cmd = _editor_command()
    try:
        proc = subprocess.run([*cmd, str(cfg_path)], check=False)
    except FileNotFoundError as exc:
        raise EivlholeError(f"editor not found: {cmd[0]}") from exc
    if proc.returncode != 0:
        err.print(f"[yellow]editor exited with status {proc.returncode}[/]")
    try:
        load_config(cfg_path)
    except ConfigError as exc:
        err.print(f"[yellow]config has problems:[/]\n{escape(str(exc))}")
        raise typer.Exit(1) from exc
    console.print("config OK")
