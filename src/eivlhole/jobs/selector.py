"""Resolve a job's `files` and `patterns` into the list of local files to send.

In recursive mode symlinked directories are not followed, while symlinks to files
are selected. Directories named `sent` (where on_success = "move" puts delivered
files) are never descended into, and an unreadable subdirectory is reported as a
warning without dropping the rest of the pattern's matches.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from eivlhole.config import JobConfig


@dataclass
class Selection:
    files: list[Path] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


SENT_DIR = "sent"


def _walk(top: Path, warnings: list[str]) -> list[Path]:
    """All entries below `top`, skipping `sent` dirs; unreadable subdirs become warnings."""

    def onerror(exc: OSError) -> None:
        if exc.filename is not None and Path(exc.filename) == top:
            raise exc  # the pattern directory itself: reported by the caller
        warnings.append(f"cannot read directory {exc.filename}: {exc}")

    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(top, onerror=onerror):
        dirnames[:] = [n for n in dirnames if n != SENT_DIR]
        found.extend(Path(dirpath) / n for n in filenames)
    return found


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
        try:
            candidates = _walk(d, sel.warnings) if pat.recursive else d.iterdir()
            matched = sorted(f for f in candidates if f.is_file() and rx.fullmatch(f.name))
        except OSError as exc:
            sel.warnings.append(f"cannot read pattern directory {pat.dir}: {exc}")
            continue
        if not matched:
            sel.warnings.append(f"pattern {pat.regex!r} in {pat.dir} matched no files")
        for f in matched:
            add(f)

    sel.files.sort(key=lambda p: (p.name, str(p)))
    return sel
