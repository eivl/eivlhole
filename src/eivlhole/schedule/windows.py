"""Windows Task Scheduler backend: XML task definition registered with schtasks."""

from __future__ import annotations

import subprocess
from datetime import date
from pathlib import Path
from xml.sax.saxutils import escape

from eivlhole import paths
from eivlhole.schedule.base import WINDOWS_DAYS, ActionResult, ScheduleSpec, run_tool

_TEMPLATE = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>eivlhole job {job}</Description>
  </RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>{start}</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByWeek>
        <DaysOfWeek>
{days}
        </DaysOfWeek>
        <WeeksInterval>1</WeeksInterval>
      </ScheduleByWeek>
    </CalendarTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>S4U</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>true</RunOnlyIfNetworkAvailable>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <ExecutionTimeLimit>PT4H</ExecutionTimeLimit>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{command}</Command>
      <Arguments>{arguments}</Arguments>
    </Exec>
  </Actions>
</Task>
"""


def task_name(job: str) -> str:
    return f"eivlhole\\{job}"


def xml_path(job: str) -> Path:
    return paths.config_dir() / "schedule" / f"{job}.xml"


class TaskScheduler:
    name = "Task Scheduler"

    def __init__(self, start_date: date | None = None):
        self.start_date = start_date

    def render(self, spec: ScheduleSpec) -> str:
        hour, minute = spec.hour_minute
        start = (self.start_date or date.today()).isoformat() + f"T{hour:02d}:{minute:02d}:00"
        days = "\n".join(f"          <{WINDOWS_DAYS[d]} />" for d in spec.days)
        command, *args = spec.command
        return _TEMPLATE.format(
            job=escape(spec.job),
            start=start,
            days=days,
            command=escape(command),
            arguments=escape(subprocess.list2cmdline(args), {'"': "&quot;"}),
        )

    def manual(self, spec: ScheduleSpec) -> str:
        return (
            f"Register by hand (as a user allowed to create tasks):\n"
            f'  schtasks /Create /TN {task_name(spec.job)} /XML "{xml_path(spec.job)}" /F\n'
            f"or import that XML file in Task Scheduler: Action > Import Task."
        )

    def install(self, spec: ScheduleSpec) -> ActionResult:
        path = xml_path(spec.job)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.render(spec).encode("utf-16"))  # BOM + UTF-16 LE
        code, out = run_tool(
            ["schtasks", "/Create", "/TN", task_name(spec.job), "/XML", str(path), "/F"]
        )
        return ActionResult(ok=code == 0, output=out, manual=self.manual(spec))

    def uninstall(self, job: str) -> ActionResult:
        code, out = run_tool(["schtasks", "/Delete", "/TN", task_name(job), "/F"])
        return ActionResult(
            ok=code == 0, output=out, manual=f"  schtasks /Delete /TN {task_name(job)} /F"
        )

    def status(self, job: str) -> str | None:
        code, out = run_tool(["schtasks", "/Query", "/TN", task_name(job), "/FO", "LIST", "/V"])
        return out if code == 0 else None
