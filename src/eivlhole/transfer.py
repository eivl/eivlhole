"""Send and receive orchestration on top of a Mailbox, with history logging."""

from __future__ import annotations

import asyncio
import getpass
import hashlib
import logging
import socket
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from eivlhole.errors import TransferError
from eivlhole.history import History, utcnow
from eivlhole.mailbox.base import Mailbox, Manifest, ManifestEntry, ProgressFn
from eivlhole.mailbox.codes import normalize_code

log = logging.getLogger(__name__)


@dataclass
class SendResult:
    transfer_id: str
    code: str
    files: list[ManifestEntry]
    run_id: int | None


@dataclass
class ReceiveResult:
    transfer_id: str
    files: list[Path]
    run_id: int | None


def collect_files(paths: Sequence[Path]) -> list[tuple[Path, str]]:
    out: list[tuple[Path, str]] = []
    for p in paths:
        p = Path(p)
        if not p.exists():
            raise TransferError(f"{p}: no such file or directory")
        if p.is_dir():
            entries = sorted(f for f in p.rglob("*") if f.is_file())
            if not entries:
                raise TransferError(f"{p}: directory is empty")
            for f in entries:
                out.append((f, (Path(p.name) / f.relative_to(p)).as_posix()))
        else:
            out.append((p, p.name))
    seen: set[str] = set()
    for _, rel in out:
        if rel in seen:
            raise TransferError(f"duplicate name in transfer: {rel}")
        seen.add(rel)
    return out


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe(history: History | None, fn: Callable[[History], object]) -> object | None:
    if history is None:
        return None
    try:
        return fn(history)
    except Exception:  # noqa: BLE001 - history must never abort a transfer
        log.exception("history write failed")
        return None


async def send(
    paths: Sequence[Path],
    mailbox: Mailbox,
    *,
    history: History | None,
    host_name: str,
    words: int = 3,
    progress: ProgressFn | None = None,
) -> SendResult:
    files = collect_files(paths)
    run_id = _safe(history, lambda h: h.start_run("send", host=host_name))
    try:
        transfer_id = await mailbox.allocate()
        entries: list[ManifestEntry] = []
        for local, rel in files:
            digest = await asyncio.to_thread(sha256_file, local)
            size = local.stat().st_size
            await mailbox.put_file(transfer_id, rel, local, progress)
            entries.append(ManifestEntry(path=rel, size=size, sha256=digest))
            _safe(history, lambda h, local=local, rel=rel, size=size, digest=digest: h.add_file(
                run_id, direction="out", filename=local.name, local_path=str(local),
                remote_path=mailbox.remote_path(transfer_id, rel), size=size, sha256=digest,
                status="sent",
            ))
        manifest = Manifest(
            created=utcnow(), sender=f"{getpass.getuser()}@{socket.gethostname()}", files=entries
        )
        await mailbox.write_manifest(transfer_id, manifest)
        code = await mailbox.publish(transfer_id, words)
    except Exception as exc:
        err = str(exc)
        _safe(history, lambda h: h.finish_run(run_id, "failed", exit_code=1, error=err))
        raise
    _safe(history, lambda h: h.finish_run(run_id, "ok"))
    log.info("sent %d file(s) as transfer %s code %s", len(entries), transfer_id, code)
    return SendResult(transfer_id=transfer_id, code=code, files=entries, run_id=run_id)


async def wait_for_receipt(
    mailbox: Mailbox,
    transfer_id: str,
    *,
    poll: float = 2.0,
    on_state: Callable[[str], None] | None = None,
) -> None:
    while not await mailbox.is_claimed(transfer_id):
        if not await mailbox.exists(transfer_id):
            raise TransferError("transfer was removed from the drop host before it was claimed")
        await asyncio.sleep(poll)
    if on_state:
        on_state("claimed")
    while await mailbox.exists(transfer_id):
        await asyncio.sleep(poll)
    if on_state:
        on_state("received")


async def receive(
    code: str,
    mailbox: Mailbox,
    out_dir: Path,
    *,
    history: History | None,
    host_name: str,
    confirm: Callable[[Manifest], bool] | None = None,
    overwrite: bool = False,
    progress: ProgressFn | None = None,
) -> ReceiveResult:
    try:
        code = normalize_code(code)
    except ValueError as exc:
        raise TransferError(str(exc)) from exc
    out_dir = Path(out_dir)
    transfer_id = await mailbox.resolve(code)
    manifest = await mailbox.read_manifest(transfer_id)

    conflicts = [e.path for e in manifest.files if (out_dir / e.path).exists()]
    if conflicts and not overwrite:
        raise TransferError(
            "refusing to overwrite, use --overwrite: "
            + ", ".join(str(out_dir / c) for c in conflicts)
            + " already exists"
        )
    if confirm is not None and not confirm(manifest):
        raise TransferError("receive cancelled; the code is still valid")

    await mailbox.claim(code, transfer_id)
    run_id = _safe(history, lambda h: h.start_run("receive", host=host_name))
    received: list[Path] = []
    failures: list[str] = []
    for entry in manifest.files:
        local = out_dir / entry.path
        remote = mailbox.remote_path(transfer_id, entry.path)
        try:
            await mailbox.get_file(transfer_id, entry.path, local, progress)
            digest = await asyncio.to_thread(sha256_file, local)
            if digest != entry.sha256:
                local.unlink(missing_ok=True)
                raise TransferError(f"checksum mismatch for {entry.path}")
        except Exception as exc:  # noqa: BLE001 - record and continue with other files
            failures.append(f"{entry.path}: {exc}")
            _safe(history, lambda h, entry=entry, local=local, remote=remote, exc=exc: h.add_file(
                run_id, direction="in", filename=Path(entry.path).name, local_path=str(local),
                remote_path=remote, size=entry.size, sha256=entry.sha256, status="failed",
                error=str(exc),
            ))
            continue
        received.append(local)
        _safe(history, lambda h, entry=entry, local=local, remote=remote: h.add_file(
            run_id, direction="in", filename=Path(entry.path).name, local_path=str(local),
            remote_path=remote, size=entry.size, sha256=entry.sha256, status="received",
        ))
    if failures:
        _safe(history, lambda h: h.finish_run(
            run_id, "failed", exit_code=3 if received else 1, error="; ".join(failures)
        ))
        raise TransferError("some files failed: " + "; ".join(failures))
    await mailbox.remove(transfer_id)
    _safe(history, lambda h: h.finish_run(run_id, "ok"))
    log.info("received %d file(s) from transfer %s", len(received), transfer_id)
    return ReceiveResult(transfer_id=transfer_id, files=received, run_id=run_id)
