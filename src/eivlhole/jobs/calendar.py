"""Decide whether a job runs on a given day: weekends, country holidays, extra dates."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from functools import cache

import holidays

from eivlhole.config import HolidayConfig
from eivlhole.errors import ConfigError

_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


@dataclass(frozen=True)
class Decision:
    skip: bool
    reason: str | None


@cache
def _country(country: str, year: int) -> holidays.HolidayBase:
    try:
        return holidays.country_holidays(country, years=[year])
    except (NotImplementedError, KeyError) as exc:
        raise ConfigError(f"unknown holiday country {country!r}") from exc


def parse_extra(entries: list[str]) -> list[tuple[date, date, str]]:
    out: list[tuple[date, date, str]] = []
    for entry in entries:
        start_s, _, end_s = entry.partition("..")
        start = date.fromisoformat(start_s.strip())
        end = date.fromisoformat(end_s.strip()) if end_s else start
        out.append((start, end, entry))
    return out


def holiday_name(day: date, cfg: HolidayConfig) -> str | None:
    if cfg.country:
        name = _country(cfg.country, day.year).get(day)
        if name:
            return str(name)
    for start, end, label in parse_extra(cfg.extra):
        if start <= day <= end:
            return f"extra: {label}"
    return None


def decide(day: date, cfg: HolidayConfig) -> Decision:
    if day.weekday() >= 5:
        return Decision(True, f"weekend ({_WEEKDAYS[day.weekday()]})")
    name = holiday_name(day, cfg)
    if name:
        return Decision(True, name)
    return Decision(False, None)


def skip_days(year: int, cfg: HolidayConfig) -> list[tuple[date, str]]:
    found: dict[date, str] = {}
    if cfg.country:
        for day, name in _country(cfg.country, year).items():
            if day.year == year:
                found[day] = str(name)
    for start, end, label in parse_extra(cfg.extra):
        day = start
        while day <= end:
            if day.year == year and day.weekday() < 5 and day not in found:
                found[day] = f"extra: {label}"
            day += timedelta(days=1)
    return sorted(found.items())
