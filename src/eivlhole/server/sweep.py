"""Expire old transfers and dangling codes. Run on the drop host as root."""

from __future__ import annotations

import logging
import re
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from eivlhole.errors import EivlholeError
from eivlhole.server.layout import CODES, TRANSFERS

log = logging.getLogger(__name__)
_DURATION_RE = re.compile(r"^(\d+)([smhdSMHD])$")
_CODE_TARGET_RE = re.compile(r"^[0-9a-f]{32}$")
_UNITS = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}


def parse_duration(text: str) -> timedelta:
    m = _DURATION_RE.match(text.strip())
    if not m:
        raise EivlholeError(f"invalid duration {text!r}; use e.g. 7d, 12h, 30m, 90s")
    return timedelta(**{_UNITS[m.group(2).lower()]: int(m.group(1))})


@dataclass
class SweepResult:
    removed_transfers: list[str] = field(default_factory=list)
    removed_codes: list[str] = field(default_factory=list)
    kept_transfers: int = 0
    errors: list[str] = field(default_factory=list)


def _check_root(root: Path) -> None:
    if not (root / TRANSFERS).is_dir() or not (root / CODES).is_dir():
        raise EivlholeError(f"{root} is not a drop root; run `eivlhole server init --root {root}`")


def _newest_mtime(path: Path) -> datetime:
    """Raises OSError (FileNotFoundError if a receiver removed `path` meanwhile)."""
    times = [path.stat().st_mtime]
    for child in path.iterdir():
        try:
            times.append(child.stat().st_mtime)
        except OSError:
            continue
    return datetime.fromtimestamp(max(times), tz=UTC)


def sweep(
    root: Path, max_age: timedelta, *, now: datetime | None = None, dry_run: bool = False
) -> SweepResult:
    _check_root(root)
    now = now or datetime.now(UTC)
    result = SweepResult()
    for d in sorted((root / TRANSFERS).iterdir()):
        if not d.is_dir():
            continue
        try:
            newest = _newest_mtime(d)
        except FileNotFoundError:
            continue  # removed by a receiver while we looked
        except OSError as exc:
            result.errors.append(f"{d}: {exc}")
            continue
        if now - newest <= max_age:
            result.kept_transfers += 1
            continue
        if dry_run:
            result.removed_transfers.append(d.name)
        elif d.is_symlink():
            try:
                d.unlink()
            except OSError as exc:
                result.errors.append(f"{d}: {exc}")
                continue
            log.warning("removed symlink entry %s", d.name)
            result.removed_transfers.append(d.name)
        else:
            errors: list[str] = []

            def _record(func, path, exc, _errors=errors):
                _errors.append(f"{path}: {exc}")

            shutil.rmtree(d, onexc=_record)
            if errors:
                result.errors.extend(errors)
            else:
                log.info("removed transfer %s", d.name)
                result.removed_transfers.append(d.name)
    for c in sorted((root / CODES).iterdir()):
        if not c.is_file():
            continue
        try:
            with c.open("rb") as fh:
                target = fh.read(64).decode("ascii", errors="replace").strip()
        except OSError:
            target = ""
        try:
            mtime = c.stat().st_mtime
        except FileNotFoundError:
            continue  # claimed by a receiver while we looked
        except OSError as exc:
            result.errors.append(f"{c}: {exc}")
            continue
        stale = not _CODE_TARGET_RE.match(target) or not (root / TRANSFERS / target).is_dir()
        old = now - datetime.fromtimestamp(mtime, tz=UTC) > max_age
        if stale or old:
            if not dry_run:
                try:
                    c.unlink(missing_ok=True)
                except OSError as exc:
                    result.errors.append(f"{c}: {exc}")
                    continue
                log.info("removed code %s", c.name)
            result.removed_codes.append(c.name)
    return result


def status(root: Path) -> dict[str, int | float | None]:
    """Count live transfers and codes.

    `claimed` counts transfers with a `claimed` marker, regardless of whether a
    receiver also left a `failed` marker in the same transfer directory.
    """
    _check_root(root)
    transfers = [d for d in (root / TRANSFERS).iterdir() if d.is_dir()]
    claimed = sum(1 for d in transfers if (d / "claimed").exists())
    codes = sum(1 for c in (root / CODES).iterdir() if c.is_file())
    oldest: float | None = None
    now = datetime.now(UTC)
    for d in transfers:
        try:
            age = (now - _newest_mtime(d)).total_seconds() / 3600
        except OSError:
            continue  # vanished while we looked
        oldest = age if oldest is None else max(oldest, age)
    return {"transfers": len(transfers), "claimed": claimed, "codes": codes, "oldest_hours": oldest}
