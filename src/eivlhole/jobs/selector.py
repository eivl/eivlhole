"""Resolve a job's `files` and `patterns` into the list of local files to send."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from eivlhole.config import JobConfig


@dataclass
class Selection:
    files: list[Path] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def select_files(job: JobConfig) -> Selection:
    sel = Selection()
    seen: set[Path] = set()

    def add(path: Path) -> None:
        key = path.resolve()
        if key not in seen:
            seen.add(key)
            sel.files.append(path)

    for entry in job.files:
        p = Path(entry).expanduser()
        if p.is_file():
            add(p)
        else:
            sel.missing.append(entry)

    for pat in job.patterns:
        d = Path(pat.dir).expanduser()
        if not d.is_dir():
            sel.warnings.append(f"pattern directory {pat.dir} does not exist")
            continue
        rx = re.compile(pat.regex)
        candidates = d.rglob("*") if pat.recursive else d.iterdir()
        matched = sorted(f for f in candidates if f.is_file() and rx.fullmatch(f.name))
        if not matched:
            sel.warnings.append(f"pattern {pat.regex!r} in {pat.dir} matched no files")
        for f in matched:
            add(f)

    sel.files.sort(key=lambda p: (p.name, str(p)))
    return sel
