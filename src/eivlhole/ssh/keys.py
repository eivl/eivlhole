"""Tool-managed ed25519 key: generate, print, enrol on a server, test."""

from __future__ import annotations

import logging
import os
import socket
from pathlib import Path

import asyncssh

from eivlhole.errors import EivlholeError
from eivlhole.ssh.client import SSHTarget, TrustPrompt, sftp

log = logging.getLogger(__name__)

AUTHORIZED_KEYS = ".ssh/authorized_keys"


def generate_keypair(
    private_path: Path, *, comment: str | None = None, force: bool = False
) -> tuple[Path, Path]:
    public_path = private_path.with_name(private_path.name + ".pub")
    if private_path.exists() and not force:
        raise EivlholeError(f"{private_path} already exists; use --force to replace it")
    private_path.parent.mkdir(parents=True, exist_ok=True)
    key = asyncssh.generate_private_key(
        "ssh-ed25519", comment=comment or f"eivlhole@{socket.gethostname()}"
    )
    key.write_private_key(str(private_path))
    key.write_public_key(str(public_path))
    if os.name == "posix":
        os.chmod(private_path, 0o600)
    log.info("generated key %s", private_path)
    return private_path, public_path


def public_key_line(public_path: Path) -> str:
    if not public_path.exists():
        raise EivlholeError(f"no public key at {public_path}; run `eivlhole keys init` first")
    return public_path.read_text(encoding="utf-8").strip().splitlines()[0]


def _same_key(a: str, b: str) -> bool:
    return a.split()[:2] == b.split()[:2]


async def enrol(
    target: SSHTarget,
    password: str,
    public_line: str,
    *,
    known_hosts: Path | None = None,
    prompt_trust: TrustPrompt | None = None,
) -> bool:
    async with sftp(
        target, password=password, known_hosts=known_hosts, prompt_trust=prompt_trust
    ) as s:
        await s.makedirs(".ssh", exist_ok=True)
        try:
            await s.chmod(".ssh", 0o700)
        except asyncssh.SFTPError:
            pass
        existing = b""
        if await s.exists(AUTHORIZED_KEYS):
            async with s.open(AUTHORIZED_KEYS, "rb") as f:
                existing = await f.read()
        text = existing.decode("utf-8", errors="replace")
        if any(line.strip() and _same_key(line, public_line) for line in text.splitlines()):
            return False
        if existing and not existing.endswith(b"\n"):
            existing += b"\n"
        async with s.open(AUTHORIZED_KEYS, "wb") as f:
            await f.write(existing + public_line.encode("utf-8") + b"\n")
        try:
            await s.chmod(AUTHORIZED_KEYS, 0o600)
        except asyncssh.SFTPError:
            pass
    log.info("enrolled public key on %s", target.label)
    return True


async def test_connection(
    target: SSHTarget,
    *,
    known_hosts: Path | None = None,
    prompt_trust: TrustPrompt | None = None,
) -> str:
    async with sftp(target, known_hosts=known_hosts, prompt_trust=prompt_trust) as s:
        return await s.getcwd()
