"""config.toml model, validation, load and save."""

from __future__ import annotations

import re
import tomllib
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import tomli_w

from eivlhole.errors import ConfigError

VALID_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
VALID_ON_SUCCESS = ("keep", "move", "delete")
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass
class HostConfig:
    name: str
    hostname: str
    user: str
    port: int = 22
    key: str = ""
    use_agent: bool = False


@dataclass
class PatternConfig:
    dir: str
    regex: str
    recursive: bool = False


@dataclass
class ScheduleConfig:
    time: str = "07:00"
    days: list[str] = field(default_factory=list)


@dataclass
class HolidayConfig:
    country: str = "NO"
    extra: list[str] = field(default_factory=list)


@dataclass
class JobConfig:
    name: str
    remote_dir: str
    host: str | None = None
    files: list[str] = field(default_factory=list)
    patterns: list[PatternConfig] = field(default_factory=list)
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)
    holidays: HolidayConfig = field(default_factory=HolidayConfig)
    on_success: str = "keep"
    retries: int = 3


@dataclass
class Defaults:
    host: str | None = None
    drop_root: str = "/srv/eivlhole"
    code_words: int = 3
    receive_dir: str = "."


@dataclass
class Config:
    defaults: Defaults = field(default_factory=Defaults)
    hosts: dict[str, HostConfig] = field(default_factory=dict)
    jobs: dict[str, JobConfig] = field(default_factory=dict)

    def host_for(self, name: str | None) -> HostConfig:
        resolved = name or self.defaults.host
        if not resolved:
            raise ConfigError("no host given and defaults.host is not set")
        if resolved not in self.hosts:
            raise ConfigError(f"unknown host '{resolved}' (see `eivlhole hosts list`)")
        return self.hosts[resolved]


class _Problems:
    def __init__(self) -> None:
        self.items: list[str] = []

    def add(self, key: str, msg: str) -> None:
        self.items.append(f"{key}: {msg}")

    def raise_if_any(self) -> None:
        if self.items:
            raise ConfigError(self.items)


def _str(data: dict, key: str, path: str, p: _Problems, *, required: bool, default: str = "") -> str:  # noqa: E501
    if key not in data:
        if required:
            p.add(f"{path}.{key}", "required")
        return default
    value = data[key]
    if not isinstance(value, str):
        p.add(f"{path}.{key}", "must be a string")
        return default
    return value


def _int(data: dict, key: str, path: str, p: _Problems, default: int) -> int:
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        p.add(f"{path}.{key}", "must be an integer")
        return default
    return value


def _bool(data: dict, key: str, path: str, p: _Problems, default: bool) -> bool:
    value = data.get(key, default)
    if not isinstance(value, bool):
        p.add(f"{path}.{key}", "must be true or false")
        return default
    return value


def _str_list(data: dict, key: str, path: str, p: _Problems) -> list[str]:
    value = data.get(key, [])
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        p.add(f"{path}.{key}", "must be a list of strings")
        return []
    return list(value)


def _valid_extra(entry: str) -> bool:
    parts = entry.split("..")
    if len(parts) not in (1, 2):
        return False
    for part in parts:
        if not _DATE_RE.match(part):
            return False
        try:
            date.fromisoformat(part)
        except ValueError:
            return False
    return True


def _parse_host(name: str, data: Any, p: _Problems) -> HostConfig:
    path = f"hosts.{name}"
    if not isinstance(data, dict):
        p.add(path, "must be a table")
        return HostConfig(name=name, hostname="", user="")
    return HostConfig(
        name=name,
        hostname=_str(data, "hostname", path, p, required=True),
        user=_str(data, "user", path, p, required=True),
        port=_int(data, "port", path, p, 22),
        key=_str(data, "key", path, p, required=False),
        use_agent=_bool(data, "use_agent", path, p, False),
    )


def _parse_job(name: str, data: Any, hosts: dict[str, HostConfig], p: _Problems) -> JobConfig:
    path = f"jobs.{name}"
    if not isinstance(data, dict):
        p.add(path, "must be a table")
        return JobConfig(name=name, remote_dir="")
    host = data.get("host")
    if host is not None:
        if not isinstance(host, str):
            p.add(f"{path}.host", "must be a string")
        elif host not in hosts:
            p.add(f"{path}.host", f"unknown host '{host}'")

    patterns: list[PatternConfig] = []
    for i, raw in enumerate(data.get("patterns", [])):
        ppath = f"{path}.patterns[{i}]"
        if not isinstance(raw, dict):
            p.add(ppath, "must be a table with dir and regex")
            continue
        regex = _str(raw, "regex", ppath, p, required=True)
        try:
            re.compile(regex)
        except re.error as exc:
            p.add(f"{ppath}.regex", f"invalid regex: {exc}")
        patterns.append(
            PatternConfig(
                dir=_str(raw, "dir", ppath, p, required=True),
                regex=regex,
                recursive=_bool(raw, "recursive", ppath, p, False),
            )
        )

    sched_raw = data.get("schedule", {})
    spath = f"{path}.schedule"
    schedule = ScheduleConfig()
    if not isinstance(sched_raw, dict):
        p.add(spath, "must be a table")
    else:
        schedule.time = _str(sched_raw, "time", spath, p, required=False, default="07:00")
        if not _TIME_RE.match(schedule.time):
            p.add(f"{spath}.time", "must be HH:MM (24-hour)")
        schedule.days = [d.lower() for d in _str_list(sched_raw, "days", spath, p)]
        for d in schedule.days:
            if d not in VALID_DAYS:
                p.add(f"{spath}.days", f'unknown day "{d}" (use {", ".join(VALID_DAYS)})')

    hol_raw = data.get("holidays", {})
    hpath = f"{path}.holidays"
    holidays = HolidayConfig()
    if not isinstance(hol_raw, dict):
        p.add(hpath, "must be a table")
    else:
        holidays.country = _str(hol_raw, "country", hpath, p, required=False, default="NO")
        holidays.extra = _str_list(hol_raw, "extra", hpath, p)
        for i, entry in enumerate(holidays.extra):
            if not _valid_extra(entry):
                p.add(f"{hpath}.extra[{i}]", "must be YYYY-MM-DD or YYYY-MM-DD..YYYY-MM-DD")

    on_success = _str(data, "on_success", path, p, required=False, default="keep")
    if on_success not in VALID_ON_SUCCESS:
        p.add(f"{path}.on_success", f"must be one of {', '.join(VALID_ON_SUCCESS)}")
    retries = _int(data, "retries", path, p, 3)
    if retries < 0:
        p.add(f"{path}.retries", "must be >= 0")

    return JobConfig(
        name=name,
        remote_dir=_str(data, "remote_dir", path, p, required=True),
        host=host if isinstance(host, str) else None,
        files=_str_list(data, "files", path, p),
        patterns=patterns,
        schedule=schedule,
        holidays=holidays,
        on_success=on_success,
        retries=retries,
    )


def parse_config(data: dict) -> Config:
    p = _Problems()
    for section in data:
        if section not in ("defaults", "hosts", "jobs"):
            p.add(section, "unknown section")

    draw = data.get("defaults", {})
    defaults = Defaults()
    if not isinstance(draw, dict):
        p.add("defaults", "must be a table")
    else:
        host = draw.get("host")
        if host is not None and not isinstance(host, str):
            p.add("defaults.host", "must be a string")
        defaults.host = host if isinstance(host, str) else None
        defaults.drop_root = _str(draw, "drop_root", "defaults", p, required=False, default="/srv/eivlhole")  # noqa: E501
        defaults.code_words = _int(draw, "code_words", "defaults", p, 3)
        if not 1 <= defaults.code_words <= 8:
            p.add("defaults.code_words", "must be between 1 and 8")
        defaults.receive_dir = _str(draw, "receive_dir", "defaults", p, required=False, default=".")

    hosts_raw = data.get("hosts", {})
    hosts: dict[str, HostConfig] = {}
    if not isinstance(hosts_raw, dict):
        p.add("hosts", "must be a table")
    else:
        for name, raw in hosts_raw.items():
            hosts[name] = _parse_host(name, raw, p)
    if defaults.host is not None and defaults.host not in hosts:
        p.add("defaults.host", f"unknown host '{defaults.host}'")

    jobs_raw = data.get("jobs", {})
    jobs: dict[str, JobConfig] = {}
    if not isinstance(jobs_raw, dict):
        p.add("jobs", "must be a table")
    else:
        for name, raw in jobs_raw.items():
            jobs[name] = _parse_job(name, raw, hosts, p)

    p.raise_if_any()
    return Config(defaults=defaults, hosts=hosts, jobs=jobs)


def to_dict(config: Config) -> dict:
    out: dict[str, Any] = {"defaults": {}}
    for k, v in asdict(config.defaults).items():
        if v is not None:
            out["defaults"][k] = v
    out["hosts"] = {}
    for name, host in config.hosts.items():
        d = asdict(host)
        d.pop("name")
        out["hosts"][name] = d
    out["jobs"] = {}
    for name, job in config.jobs.items():
        d = asdict(job)
        d.pop("name")
        if d["host"] is None:
            d.pop("host")
        out["jobs"][name] = d
    return out


def load_config(path: Path) -> Config:
    if not path.exists():
        return Config()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: {exc}") from exc
    return parse_config(data)


def save_config(config: Config, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(tomli_w.dumps(to_dict(config)), encoding="utf-8")
