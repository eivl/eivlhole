"""Unattended delivery of a job's files to its host. Never prompts."""

from __future__ import annotations

import asyncio
import logging
import posixpath
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import asyncssh

from eivlhole.config import Config
from eivlhole.errors import ConfigError, EivlholeError
from eivlhole.history import History
from eivlhole.jobs.calendar import decide
from eivlhole.jobs.selector import select_files
from eivlhole.mailbox.base import ProgressFn
from eivlhole.ssh.client import sftp, target_from_host
from eivlhole.transfer import _safe, sha256_file

log = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_SKIPPED = 2
EXIT_PARTIAL = 3
BACKOFF = (1.0, 2.0, 4.0)
BLOCK_SIZE = 65536

SleepFn = Callable[[float], Awaitable[None]]


@dataclass
class FileOutcome:
    local: Path
    remote: str
    status: str  # pending | sent | failed
    error: str | None = None
    attempts: int = 0
    size: int = 0
    sha256: str = ""


@dataclass
class RunResult:
    job: str
    host: str | None
    status: str  # ok | partial | failed | skipped | dry-run
    exit_code: int
    outcomes: list[FileOutcome] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    reason: str | None = None
    run_id: int | None = None

    @property
    def sent(self) -> list[FileOutcome]:
        return [o for o in self.outcomes if o.status == "sent"]

    @property
    def failed(self) -> list[FileOutcome]:
        return [o for o in self.outcomes if o.status == "failed"]


async def replace_remote(sftp_client: asyncssh.SFTPClient, part: str, final: str) -> None:
    """Atomically put `part` in place of `final`; fall back when posix-rename is unsupported."""
    try:
        await sftp_client.posix_rename(part, final)
    except asyncssh.SFTPOpUnsupported:
        log.debug("server lacks posix-rename; using remove+rename for %s", final)
        try:
            await sftp_client.remove(final)
        except asyncssh.SFTPNoSuchFile:
            pass
        await sftp_client.rename(part, final)


async def upload_file(
    sftp_client: asyncssh.SFTPClient,
    local: Path,
    remote: str,
    *,
    retries: int,
    sleep: SleepFn = asyncio.sleep,
    progress: ProgressFn | None = None,
) -> int:
    """Upload with .part + atomic replace, retrying on transient errors. Returns attempts used."""
    part = remote + ".part"

    def handler(_src: bytes, _dst: bytes, done: int, total: int) -> None:
        if progress is not None:
            progress(local.name, done, total)

    attempt = 0
    while True:
        attempt += 1
        try:
            await sftp_client.put(str(local), part, block_size=BLOCK_SIZE, progress_handler=handler)
            await replace_remote(sftp_client, part, remote)
            return attempt
        except (OSError, asyncssh.Error) as exc:
            if attempt > retries:
                raise
            delay = BACKOFF[min(attempt - 1, len(BACKOFF) - 1)]
            log.warning(
                "upload of %s failed (attempt %d): %s; retrying in %gs",
                local.name,
                attempt,
                exc,
                delay,
            )
            await sleep(delay)


def apply_on_success(policy: str, local: Path) -> None:
    if policy == "keep":
        return
    if policy == "delete":
        local.unlink(missing_ok=True)
        return
    if policy == "move":
        dest_dir = local.parent / "sent"
        dest_dir.mkdir(exist_ok=True)
        local.replace(dest_dir / local.name)
        return
    raise ConfigError(f"unknown on_success policy {policy!r}")


async def run_job(
    name: str,
    config: Config,
    *,
    history: History | None,
    today: date | None = None,
    force: bool = False,
    dry_run: bool = False,
    known_hosts: Path | None = None,
    progress: ProgressFn | None = None,
    sleep: SleepFn = asyncio.sleep,
) -> RunResult:
    job = config.jobs.get(name)
    if job is None:
        raise ConfigError(f"unknown job '{name}' (see `eivlhole jobs list`)")
    host = config.host_for(job.host)
    day = today or date.today()

    if not force:
        decision = decide(day, job.holidays)
        if decision.skip:
            result = RunResult(
                job=name,
                host=host.name,
                status="skipped",
                exit_code=EXIT_SKIPPED,
                reason=decision.reason,
            )
            if not dry_run:
                run_id = _safe(history, lambda h: h.start_run("job", job=name, host=host.name))
                _safe(
                    history,
                    lambda h: h.finish_run(
                        run_id, "skipped", exit_code=EXIT_SKIPPED, error=decision.reason
                    ),
                )
                result.run_id = run_id
            log.info("job %s skipped: %s", name, decision.reason)
            return result

    selection = select_files(job)
    outcomes = [
        FileOutcome(local=f, remote=posixpath.join(job.remote_dir, f.name), status="pending")
        for f in selection.files
    ]
    outcomes += [
        FileOutcome(
            local=Path(m),
            remote=posixpath.join(job.remote_dir, Path(m).name),
            status="failed",
            error="no such file",
        )
        for m in selection.missing
    ]
    result = RunResult(
        job=name,
        host=host.name,
        status="dry-run",
        exit_code=EXIT_OK,
        outcomes=outcomes,
        warnings=selection.warnings,
    )
    if dry_run:
        return result

    run_id = _safe(history, lambda h: h.start_run("job", job=name, host=host.name))
    result.run_id = run_id
    error: str | None = None
    try:
        if not selection.files and not selection.missing:
            error = "no files matched the job's files or patterns"
        else:
            target = target_from_host(host)
            try:
                async with sftp(target, known_hosts=known_hosts, prompt_trust=None) as s:
                    await s.makedirs(job.remote_dir, exist_ok=True)
                    for o in outcomes:
                        if o.status != "pending":
                            continue
                        try:
                            o.size = o.local.stat().st_size
                            o.sha256 = await asyncio.to_thread(sha256_file, o.local)
                            o.attempts = await upload_file(
                                s,
                                o.local,
                                o.remote,
                                retries=job.retries,
                                sleep=sleep,
                                progress=progress,
                            )
                            o.status = "sent"
                            apply_on_success(job.on_success, o.local)
                        except (OSError, asyncssh.Error, EivlholeError) as exc:
                            o.status = "failed"
                            o.error = str(exc) or type(exc).__name__
                            log.error("job %s: %s failed: %s", name, o.local, o.error)
            except EivlholeError as exc:
                error = str(exc)
                for o in outcomes:
                    if o.status == "pending":
                        o.status, o.error = "failed", error
        for o in outcomes:
            _safe(
                history,
                lambda h, o=o: h.add_file(
                    run_id,
                    direction="out",
                    filename=o.local.name,
                    local_path=str(o.local),
                    remote_path=o.remote,
                    size=o.size,
                    sha256=o.sha256,
                    status=o.status,
                    error=o.error,
                ),
            )
        sent, failed = result.sent, result.failed
        if sent and not failed and error is None:
            result.status, result.exit_code = "ok", EXIT_OK
        elif sent:
            result.status, result.exit_code = "partial", EXIT_PARTIAL
        else:
            result.status, result.exit_code = "failed", EXIT_FAILED
        if error is None and failed:
            error = "; ".join(f"{o.local.name}: {o.error}" for o in failed)
        result.reason = error
    finally:
        _safe(
            history,
            lambda h: h.finish_run(
                run_id,
                result.status if result.status != "dry-run" else "failed",
                exit_code=result.exit_code,
                error=error,
            ),
        )
    log.info(
        "job %s finished: %s (%d sent, %d failed)",
        name,
        result.status,
        len(result.sent),
        len(result.failed),
    )
    return result
