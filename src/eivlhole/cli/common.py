"""Shared CLI state, error handling and prompts."""

from __future__ import annotations

import asyncio
import functools
import logging
import os
import sys
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.prompt import Confirm

from eivlhole import paths
from eivlhole.config import Config, load_config, save_config
from eivlhole.errors import EivlholeError
from eivlhole.history import History
from eivlhole.ssh.client import SSHTarget, TrustPrompt

console = Console()
err = Console(stderr=True)


@dataclass
class State:
    unattended: bool = False
    verbose: bool = False
    _config: Config | None = field(default=None, repr=False)

    @property
    def config(self) -> Config:
        if self._config is None:
            self._config = load_config(paths.config_file())
        return self._config

    def save(self) -> None:
        save_config(self.config, paths.config_file())

    def reload(self) -> None:
        self._config = None

    def history(self) -> History:
        return History(paths.history_file())

    def known_hosts(self) -> Path:
        return paths.known_hosts_file()

    def trust_prompt(self) -> TrustPrompt | None:
        if self.unattended:
            return None

        def prompt(target: SSHTarget, fp: str) -> bool:
            console.print(f"The authenticity of host [bold]{target.label}[/] can't be established.")
            console.print(f"Host key fingerprint: [bold]{fp}[/]")
            return Confirm.ask("Trust this host and remember its key?", default=False)

        return prompt

    def confirm(self, question: str, *, default: bool = False) -> bool:
        if self.unattended:
            return default
        return Confirm.ask(question, default=default)


def get_state(ctx: typer.Context) -> State:
    return ctx.ensure_object(State)


def run_async[T](coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


class _StderrHandler(logging.StreamHandler):
    """Resolves sys.stderr at emit time, so test harnesses that swap stderr keep working."""

    @property
    def stream(self):  # type: ignore[override]
        return sys.stderr

    @stream.setter
    def stream(self, value: object) -> None:
        pass


_file_handler: RotatingFileHandler | None = None
_stderr_handler: _StderrHandler | None = None
_FMT = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")


def setup_logging(verbose: bool) -> None:
    """Idempotent. Re-points the file handler if the config dir changed (tests, --config-dir)."""
    global _file_handler, _stderr_handler
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    log_path = os.path.abspath(paths.log_dir() / "eivlhole.log")
    if _file_handler is not None and _file_handler.baseFilename != log_path:
        root.removeHandler(_file_handler)
        _file_handler.close()
        _file_handler = None
    if _file_handler is None:
        _file_handler = RotatingFileHandler(
            log_path, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        _file_handler.setFormatter(_FMT)
        _file_handler.setLevel(logging.INFO)
        root.addHandler(_file_handler)
    if _stderr_handler is None:
        _stderr_handler = _StderrHandler()
        _stderr_handler.setFormatter(_FMT)
        root.addHandler(_stderr_handler)
    _stderr_handler.setLevel(logging.DEBUG if verbose else logging.WARNING)
    logging.getLogger("asyncssh").setLevel(logging.WARNING)


def handle_errors[T](fn: Callable[..., T]) -> Callable[..., T]:
    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> T:
        try:
            return fn(*args, **kwargs)
        except EivlholeError as exc:
            logging.getLogger("eivlhole").error("%s", exc)
            err.print(f"[red]error:[/] {exc}")
            raise typer.Exit(exc.exit_code) from exc
        except KeyboardInterrupt as exc:
            err.print("interrupted")
            raise typer.Exit(130) from exc

    return wrapper
