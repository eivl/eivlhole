"""Scheduler protocol and the pieces shared by the Task Scheduler and cron backends."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from eivlhole.config import JobConfig
from eivlhole.errors import EivlholeError

CRON_DAYS = {"mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6, "sun": 0}
WINDOWS_DAYS = {
    "mon": "Monday",
    "tue": "Tuesday",
    "wed": "Wednesday",
    "thu": "Thursday",
    "fri": "Friday",
    "sat": "Saturday",
    "sun": "Sunday",
}
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


@dataclass
class ScheduleSpec:
    job: str
    time: str
    days: list[str]
    command: list[str]

    @property
    def hour_minute(self) -> tuple[int, int]:
        m = _TIME_RE.fullmatch(self.time)
        if not m:
            raise EivlholeError(f"schedule.time must be HH:MM, got {self.time!r}")
        return int(m.group(1)), int(m.group(2))


@dataclass
class ActionResult:
    ok: bool
    output: str
    manual: str


def default_run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    """Indirection over subprocess.run so tests can replace it."""
    return subprocess.run(args, **kwargs)


def run_tool(args: list[str], *, input_text: str | None = None) -> tuple[int | None, str]:
    """Run a scheduler binary. Returns (returncode or None if missing, combined output)."""
    kwargs: dict[str, Any] = {"capture_output": True, "text": True}
    if input_text is not None:
        kwargs["input"] = input_text
    try:
        proc = default_run(args, **kwargs)
    except FileNotFoundError:
        return None, f"{args[0]}: not found on this machine"
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, out.strip()


def resolve_command() -> list[str]:
    exe = shutil.which("eivlhole")
    if exe:
        return [exe]
    return [sys.executable, "-m", "eivlhole"]


def build_spec(job: JobConfig, config_dir: Path) -> ScheduleSpec:
    if not job.schedule.days:
        raise EivlholeError(
            f'job \'{job.name}\' has no schedule.days; add e.g. days = ["tue", "thu"]'
        )
    spec = ScheduleSpec(
        job=job.name,
        time=job.schedule.time,
        days=list(job.schedule.days),
        command=resolve_command()
        + ["--config-dir", str(config_dir), "--unattended", "run", job.name],
    )
    _ = spec.hour_minute  # validates time
    return spec


class Scheduler(Protocol):
    name: str

    def render(self, spec: ScheduleSpec) -> str: ...
    def install(self, spec: ScheduleSpec) -> ActionResult: ...
    def uninstall(self, job: str) -> ActionResult: ...
    def status(self, job: str) -> str | None: ...
    def manual(self, spec: ScheduleSpec) -> str: ...


def get_scheduler(platform: str | None = None) -> Scheduler:
    from eivlhole.schedule.linux import CronScheduler
    from eivlhole.schedule.windows import TaskScheduler

    plat = platform or ("windows" if os.name == "nt" else "linux")
    return TaskScheduler() if plat == "windows" else CronScheduler()
