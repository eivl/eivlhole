"""Read-only view models for the TUI. No Textual imports, so this is plain unit-testable code."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import tomli_w

from eivlhole.cli.history_cmd import _local as fmt_local
from eivlhole.cli.run_cmd import parse_date
from eivlhole.config import Config, to_dict
from eivlhole.history import FileRecord, History, RunRecord
from eivlhole.ssh.client import find_pinned, target_from_host


@dataclass
class JobRow:
    name: str
    host: str
    remote_dir: str
    days: str
    time: str
    sources: str
    on_success: str


@dataclass
class HostRow:
    name: str
    hostname: str
    port: int
    user: str
    trusted: bool
    default: bool


@dataclass
class HistoryFilter:
    names: list[str] = field(default_factory=list)
    on: date | None = None
    since: date | None = None
    until: date | None = None

    @property
    def active(self) -> bool:
        return bool(self.names or self.on or self.since or self.until)

    def describe(self) -> str:
        if not self.active:
            return "all runs"
        parts = list(self.names)
        if self.on:
            parts.append(f"on {self.on}")
        if self.since or self.until:
            parts.append(f"{self.since or ''}..{self.until or ''}")
        return "files matching " + " ".join(parts)


def _looks_like_date(token: str) -> bool:
    low = token.lower()
    return low in ("today", "yesterday") or (
        len(token) == 10 and token[4] == "-" and token[7] == "-"
    )


def parse_history_filter(text: str) -> HistoryFilter:
    flt = HistoryFilter()
    for token in text.split():
        if ".." in token:
            start, _, end = token.partition("..")
            flt.since, flt.until = parse_date(start), parse_date(end)
        elif _looks_like_date(token):
            flt.on = parse_date(token)
        else:
            flt.names.append(token)
    return flt


def job_rows(cfg: Config) -> list[JobRow]:
    rows = []
    for job in cfg.jobs.values():
        sources = (
            f"{len(job.files)} file{'s' if len(job.files) != 1 else ''}, "
            f"{len(job.patterns)} pattern{'s' if len(job.patterns) != 1 else ''}"
        )
        rows.append(
            JobRow(
                name=job.name,
                host=job.host or (cfg.defaults.host or "?"),
                remote_dir=job.remote_dir,
                days=",".join(job.schedule.days) or "-",
                time=job.schedule.time,
                sources=sources,
                on_success=job.on_success,
            )
        )
    return rows


def host_rows(cfg: Config, known_hosts: Path) -> list[HostRow]:
    rows = []
    for host in cfg.hosts.values():
        pinned = find_pinned(target_from_host(host), known_hosts) is not None
        rows.append(
            HostRow(
                name=host.name,
                hostname=host.hostname,
                port=host.port,
                user=host.user,
                trusted=pinned,
                default=cfg.defaults.host == host.name,
            )
        )
    return rows


def run_rows(history: History, limit: int = 200) -> list[RunRecord]:
    return history.runs(limit=limit)


def file_rows(history: History, flt: HistoryFilter, limit: int = 200) -> list[FileRecord]:
    return history.files(flt.names, on=flt.on, since=flt.since, until=flt.until, limit=limit)


def job_detail(cfg: Config, name: str) -> str:
    return tomli_w.dumps({"jobs": {name: to_dict(cfg)["jobs"][name]}})


def host_detail(cfg: Config, name: str, known_hosts: Path) -> str:
    host = cfg.hosts[name]
    trusted = find_pinned(target_from_host(host), known_hosts) is not None
    hostkey = "pinned" if trusted else f"not trusted (run: eivlhole hosts trust {host.name})"
    lines = [
        f"host {host.name}",
        f"  {host.user}@{host.hostname}:{host.port}",
        f"  key: {host.key or '(tool key)'}",
        f"  agent: {'yes' if host.use_agent else 'no'}",
        f"  host key: {hostkey}",
        f"  default: {'yes' if cfg.defaults.host == host.name else 'no'}",
    ]
    return "\n".join(lines)


def run_detail(history: History, run_id: int) -> str:
    run = history.run(run_id)
    if run is None:
        return f"no run with id {run_id}"
    lines = [
        f"run {run.id}: {run.kind} {run.job or ''} on {run.host or ''}".rstrip(),
        f"  started  {fmt_local(run.started)}",
        f"  finished {fmt_local(run.finished)}",
        f"  status   {run.status}"
        + (f" (exit {run.exit_code})" if run.exit_code is not None else ""),
    ]
    if run.error:
        lines.append(f"  error    {run.error}")
    lines.append("")
    for f in history.files_for_run(run_id):
        lines.append(
            f"  {f.direction:3} {f.status:8} {f.filename}" + (f"  ({f.error})" if f.error else "")
        )
    return "\n".join(lines)


def file_detail(rec: FileRecord) -> str:
    return "\n".join(
        [
            f"file {rec.filename}",
            f"  when    {fmt_local(rec.ts)}",
            f"  run     {rec.run_id}",
            f"  dir     {rec.direction}",
            f"  status  {rec.status}",
            f"  local   {rec.local_path or ''}",
            f"  remote  {rec.remote_path or ''}",
            f"  size    {rec.size or 0}",
            f"  sha256  {rec.sha256 or ''}",
        ]
        + ([f"  error   {rec.error}"] if rec.error else [])
    )
