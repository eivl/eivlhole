"""Linux backend: one tagged line in the user's crontab."""

from __future__ import annotations

import shlex

from eivlhole.errors import EivlholeError
from eivlhole.schedule.base import CRON_DAYS, ActionResult, ScheduleSpec, run_tool

TAG = "# eivlhole:"


class CrontabReadError(EivlholeError):
    """`crontab -l` failed for a reason other than 'no crontab yet'."""


def read_crontab() -> str:
    code, out, err = run_tool(["crontab", "-l"])
    if code is None:
        raise FileNotFoundError("crontab")
    if code == 0:
        return out + ("\n" if out and not out.endswith("\n") else "")
    if code == 1 and "no crontab" in err.lower():
        return ""
    raise CrontabReadError(err.strip() or f"crontab -l exited with code {code}")


def write_crontab(text: str) -> tuple[int | None, str]:
    if text and not text.endswith("\n"):
        text += "\n"
    code, out, err = run_tool(["crontab", "-"], input_text=text)
    return code, (out + err).strip()


def _without_job(text: str, job: str) -> str:
    keep = [ln for ln in text.splitlines() if not ln.rstrip().endswith(f"{TAG}{job}")]
    return "\n".join(keep) + ("\n" if keep else "")


class CronScheduler:
    name = "cron"

    def render(self, spec: ScheduleSpec) -> str:
        hour, minute = spec.hour_minute
        days = ",".join(str(CRON_DAYS[d]) for d in spec.days)
        command = shlex.join(spec.command).replace("%", "\\%")
        return f"{minute} {hour} * * {days} {command}  {TAG}{spec.job}"

    def manual(self, spec: ScheduleSpec) -> str:
        return f"Add this line with `crontab -e`:\n  {self.render(spec)}"

    def install(self, spec: ScheduleSpec) -> ActionResult:
        try:
            current = read_crontab()
        except FileNotFoundError:
            return ActionResult(False, "crontab: not found on this machine", self.manual(spec))
        except CrontabReadError as exc:
            return ActionResult(
                False, f"could not read the current crontab: {exc}", self.manual(spec)
            )
        new = _without_job(current, spec.job) + self.render(spec) + "\n"
        code, out = write_crontab(new)
        return ActionResult(ok=code == 0, output=out, manual=self.manual(spec))

    def uninstall(self, job: str) -> ActionResult:
        try:
            current = read_crontab()
        except FileNotFoundError:
            return ActionResult(
                False,
                "crontab: not found on this machine",
                f"Remove the line ending in `{TAG}{job}` with `crontab -e`.",
            )
        except CrontabReadError as exc:
            return ActionResult(
                False,
                f"could not read the current crontab: {exc}",
                f"Remove the line ending in `{TAG}{job}` with `crontab -e`.",
            )
        code, out = write_crontab(_without_job(current, job))
        return ActionResult(
            ok=code == 0,
            output=out,
            manual=f"Remove the line ending in `{TAG}{job}` with `crontab -e`.",
        )

    def status(self, job: str) -> str | None:
        try:
            current = read_crontab()
        except (FileNotFoundError, CrontabReadError):
            return None
        for ln in current.splitlines():
            if ln.rstrip().endswith(f"{TAG}{job}"):
                return ln
        return None
