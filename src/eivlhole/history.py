"""Transaction log: every run and every file movement, in sqlite."""

from __future__ import annotations

import getpass
import socket
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from pathlib import Path

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,
    started TEXT NOT NULL,
    finished TEXT,
    kind TEXT NOT NULL,
    job TEXT,
    host TEXT,
    status TEXT NOT NULL,
    exit_code INTEGER,
    error TEXT,
    machine TEXT,
    username TEXT
);
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES runs(id),
    ts TEXT NOT NULL,
    direction TEXT NOT NULL,
    filename TEXT NOT NULL,
    local_path TEXT,
    remote_path TEXT,
    size INTEGER,
    sha256 TEXT,
    status TEXT NOT NULL,
    error TEXT
);
CREATE INDEX IF NOT EXISTS files_name_ts ON files(filename, ts);
CREATE INDEX IF NOT EXISTS files_ts ON files(ts);
"""


def utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _day_bounds(day: date) -> tuple[str, str]:
    """UTC ISO bounds [start, end) of a local calendar day."""
    start = datetime.combine(day, time.min).astimezone().astimezone(UTC)
    end = datetime.combine(day, time.max).astimezone().astimezone(UTC)
    return start.isoformat(timespec="seconds"), end.isoformat(timespec="microseconds")


def _is_glob(name: str) -> bool:
    return "*" in name or "?" in name


@dataclass
class RunRecord:
    id: int
    started: str
    finished: str | None
    kind: str
    job: str | None
    host: str | None
    status: str
    exit_code: int | None
    error: str | None
    machine: str | None
    username: str | None


@dataclass
class FileRecord:
    id: int
    run_id: int
    ts: str
    direction: str
    filename: str
    local_path: str | None
    remote_path: str | None
    size: int | None
    sha256: str | None
    status: str
    error: str | None


class History:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        row = self._conn.execute("SELECT version FROM schema_version").fetchone()
        if row is None:
            self._conn.execute("INSERT INTO schema_version VALUES (?)", (SCHEMA_VERSION,))
        self._conn.commit()

    def __enter__(self) -> History:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._conn.close()

    # --- writes -------------------------------------------------------

    def start_run(self, kind: str, *, job: str | None = None, host: str | None = None) -> int:
        cur = self._conn.execute(
            "INSERT INTO runs (started, kind, job, host, status, machine, username)"
            " VALUES (?, ?, ?, ?, 'running', ?, ?)",
            (utcnow(), kind, job, host, socket.gethostname(), getpass.getuser()),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def finish_run(
        self, run_id: int, status: str, *, exit_code: int = 0, error: str | None = None
    ) -> None:
        self._conn.execute(
            "UPDATE runs SET finished=?, status=?, exit_code=?, error=? WHERE id=?",
            (utcnow(), status, exit_code, error, run_id),
        )
        self._conn.commit()

    def add_file(
        self,
        run_id: int,
        *,
        direction: str,
        filename: str,
        local_path: str,
        remote_path: str,
        size: int,
        sha256: str,
        status: str,
        error: str | None = None,
    ) -> int:
        cur = self._conn.execute(
            "INSERT INTO files (run_id, ts, direction, filename, local_path, remote_path,"
            " size, sha256, status, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                utcnow(),
                direction,
                filename,
                local_path,
                remote_path,
                size,
                sha256,
                status,
                error,
            ),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def update_file_error(self, file_id: int, error: str | None) -> None:
        self._conn.execute("UPDATE files SET error=? WHERE id=?", (error, file_id))
        self._conn.commit()

    # --- reads --------------------------------------------------------

    def files(
        self,
        names: Sequence[str] = (),
        *,
        on: date | None = None,
        since: date | None = None,
        until: date | None = None,
        job: str | None = None,
        host: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[FileRecord]:
        """Query file movements, newest first.

        A name is a glob if and only if it contains ``*`` or ``?``; otherwise it
        is matched exactly. Bracket-only patterns such as ``report[12].csv`` are
        therefore exact names (add a ``*`` to make them a glob).
        """
        where: list[str] = []
        params: list[object] = []
        if names:
            where.append(
                "("
                + " OR ".join(
                    "f.filename GLOB ?" if _is_glob(n) else "f.filename = ?" for n in names
                )
                + ")"
            )
            params.extend(names)
        if on is not None:
            lo, hi = _day_bounds(on)
            where.append("f.ts >= ? AND f.ts <= ?")
            params.extend([lo, hi])
        if since is not None:
            where.append("f.ts >= ?")
            params.append(_day_bounds(since)[0])
        if until is not None:
            where.append("f.ts <= ?")
            params.append(_day_bounds(until)[1])
        if job is not None:
            where.append("r.job = ?")
            params.append(job)
        if host is not None:
            where.append("r.host = ?")
            params.append(host)
        if status is not None:
            where.append("f.status = ?")
            params.append(status)
        sql = "SELECT f.* FROM files f JOIN runs r ON r.id = f.run_id"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY f.ts DESC, f.id DESC LIMIT ?"
        params.append(limit)
        return [FileRecord(**dict(row)) for row in self._conn.execute(sql, params)]

    def runs(
        self,
        *,
        job: str | None = None,
        since: date | None = None,
        until: date | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[RunRecord]:
        where: list[str] = []
        params: list[object] = []
        if job is not None:
            where.append("job = ?")
            params.append(job)
        if since is not None:
            where.append("started >= ?")
            params.append(_day_bounds(since)[0])
        if until is not None:
            where.append("started <= ?")
            params.append(_day_bounds(until)[1])
        if status is not None:
            where.append("status = ?")
            params.append(status)
        sql = "SELECT * FROM runs"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY started DESC, id DESC LIMIT ?"
        params.append(limit)
        return [RunRecord(**dict(row)) for row in self._conn.execute(sql, params)]

    def run(self, run_id: int) -> RunRecord | None:
        row = self._conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return RunRecord(**dict(row)) if row else None

    def files_for_run(self, run_id: int) -> list[FileRecord]:
        rows = self._conn.execute("SELECT * FROM files WHERE run_id = ? ORDER BY id", (run_id,))
        return [FileRecord(**dict(row)) for row in rows]
