"""Mailbox stored as files on a plain OpenSSH drop host, accessed over SFTP."""

from __future__ import annotations

import logging
import posixpath
import re
import secrets
from pathlib import Path

import asyncssh

from eivlhole.errors import CodeNotFound, MailboxError
from eivlhole.mailbox.base import Manifest, ProgressFn, validate_relpath
from eivlhole.mailbox.codes import generate_code

log = logging.getLogger(__name__)

TRANSFER_ID_RE = re.compile(r"^[0-9a-f]{32}$")
BLOCK_SIZE = 65536
MANIFEST = "manifest.json"
CLAIMED = "claimed"
FAILED = "failed"


CODE_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def _check_code(code: str) -> None:
    if not CODE_RE.match(code):
        raise MailboxError(f"invalid code {code!r}")


def _check_transfer_id(tid: str) -> None:
    if not TRANSFER_ID_RE.match(tid):
        raise MailboxError("invalid transfer id")


class SftpMailbox:
    def __init__(self, sftp: asyncssh.SFTPClient, root: str):
        self.sftp = sftp
        self.root = root.rstrip("/") or "/"

    # --- paths ---------------------------------------------------------

    def _code_path(self, code: str) -> str:
        _check_code(code)
        return posixpath.join(self.root, "codes", code)

    def _transfer_dir(self, transfer_id: str) -> str:
        _check_transfer_id(transfer_id)
        return posixpath.join(self.root, "transfers", transfer_id)

    def remote_path(self, transfer_id: str, relpath: str) -> str:
        validate_relpath(relpath)
        return posixpath.join(self._transfer_dir(transfer_id), relpath)

    # --- sending -------------------------------------------------------

    async def allocate(self) -> str:
        transfer_id = secrets.token_hex(16)
        try:
            await self.sftp.mkdir(self._transfer_dir(transfer_id))
        except asyncssh.SFTPError as exc:
            raise MailboxError(
                f"cannot create {self._transfer_dir(transfer_id)}: {exc}. "
                f"Has `eivlhole server init` been run on the drop host, and is "
                f"defaults.drop_root correct?"
            ) from exc
        return transfer_id

    async def put_file(
        self, transfer_id: str, relpath: str, local: Path, progress: ProgressFn | None = None
    ) -> None:
        remote = self.remote_path(transfer_id, relpath)
        parent = posixpath.dirname(remote)
        if parent != self._transfer_dir(transfer_id):
            await self.sftp.makedirs(parent, exist_ok=True)

        def handler(_src: bytes, _dst: bytes, done: int, total: int) -> None:
            if progress is not None:
                progress(relpath, done, total)

        try:
            await self.sftp.put(
                str(local), remote + ".part", progress_handler=handler, block_size=BLOCK_SIZE
            )
            await self.sftp.rename(remote + ".part", remote)
        except asyncssh.SFTPError as exc:
            raise MailboxError(f"upload of {relpath} failed: {exc}") from exc

    async def write_manifest(self, transfer_id: str, manifest: Manifest) -> None:
        path = self.remote_path(transfer_id, MANIFEST)
        try:
            async with self.sftp.open(path, "wb") as f:
                await f.write(manifest.to_json().encode("utf-8"))
        except asyncssh.SFTPError as exc:
            raise MailboxError(f"writing manifest failed: {exc}") from exc

    async def publish(self, transfer_id: str, words: int) -> str:
        for _ in range(10):
            code = generate_code(words)
            try:
                async with self.sftp.open(self._code_path(code), "xb") as f:
                    await f.write((transfer_id + "\n").encode("ascii"))
                return code
            except asyncssh.SFTPFailure:
                log.debug("code %s already taken, retrying", code)
                continue
            except asyncssh.SFTPError as exc:
                raise MailboxError(
                    f"cannot create code file under {self.root}/codes: {exc}"
                ) from exc
        raise MailboxError("could not allocate a unique code after 10 attempts")

    # --- receiving -----------------------------------------------------

    async def resolve(self, code: str) -> str:
        try:
            async with self.sftp.open(self._code_path(code), "rb") as f:
                text = (await f.read()).decode("ascii", errors="replace").strip()
        except asyncssh.SFTPError as exc:
            if isinstance(exc, asyncssh.SFTPNoSuchFile) and await self.sftp.isdir(
                posixpath.join(self.root, "codes")
            ):
                raise CodeNotFound(
                    f"code {code} not found: it may be mistyped, already claimed, or expired"
                ) from exc
            raise MailboxError(
                f"cannot read code under {self.root}/codes: {exc}. "
                f"Has `eivlhole server init` been run, and is defaults.drop_root correct?"
            ) from exc
        if not TRANSFER_ID_RE.match(text):
            raise MailboxError(f"code {code} points at a malformed transfer id")
        return text

    async def claim(self, code: str, transfer_id: str) -> None:
        try:
            await self.sftp.rename(
                self._code_path(code), self.remote_path(transfer_id, CLAIMED)
            )
        except (asyncssh.SFTPNoSuchFile, asyncssh.SFTPFailure) as exc:
            raise CodeNotFound(f"code {code} was claimed by someone else or expired") from exc
        except asyncssh.SFTPError as exc:
            raise MailboxError(f"cannot claim code {code}: {exc}") from exc

    async def read_manifest(self, transfer_id: str) -> Manifest:
        try:
            async with self.sftp.open(self.remote_path(transfer_id, MANIFEST), "rb") as f:
                data = await f.read()
        except asyncssh.SFTPError as exc:
            raise MailboxError(
                "transfer has no manifest: the sender has not finished uploading, "
                "or the transfer was removed"
            ) from exc
        try:
            return Manifest.from_json(data.decode("utf-8"))
        except (ValueError, KeyError, TypeError) as exc:
            raise MailboxError("transfer manifest is corrupt") from exc

    async def get_file(
        self, transfer_id: str, relpath: str, local: Path, progress: ProgressFn | None = None
    ) -> None:
        local.parent.mkdir(parents=True, exist_ok=True)

        def handler(_src: bytes, _dst: bytes, done: int, total: int) -> None:
            if progress is not None:
                progress(relpath, done, total)

        try:
            await self.sftp.get(
                self.remote_path(transfer_id, relpath), str(local),
                progress_handler=handler, block_size=BLOCK_SIZE,
            )
        except asyncssh.SFTPError as exc:
            local.unlink(missing_ok=True)
            raise MailboxError(f"download of {relpath} failed: {exc}") from exc

    async def is_claimed(self, transfer_id: str) -> bool:
        return await self.sftp.exists(self.remote_path(transfer_id, CLAIMED))

    async def exists(self, transfer_id: str) -> bool:
        return await self.sftp.isdir(self._transfer_dir(transfer_id))

    async def remove(self, transfer_id: str) -> None:
        try:
            await self.sftp.rmtree(self._transfer_dir(transfer_id))
        except asyncssh.SFTPNoSuchFile:
            return
        except asyncssh.SFTPError as exc:
            log.warning("could not remove transfer %s: %s", transfer_id, exc)

    async def mark_failed(self, transfer_id: str, message: str) -> None:
        try:
            async with self.sftp.open(self.remote_path(transfer_id, FAILED), "wb") as f:
                await f.write(message.encode("utf-8", errors="replace"))
        except asyncssh.SFTPError as exc:
            log.warning("could not mark transfer %s failed: %s", transfer_id, exc)

    async def failure(self, transfer_id: str) -> str | None:
        try:
            async with self.sftp.open(self.remote_path(transfer_id, FAILED), "rb") as f:
                return (await f.read()).decode("utf-8", errors="replace")
        except asyncssh.SFTPError:
            return None
