"""Mailbox protocol: how codes map to transfers on the drop host."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Protocol

from eivlhole.errors import MailboxError

ProgressFn = Callable[[str, int, int], None]


_FORBIDDEN_CHARS = set(':<>"|?*\\')
_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {f"{p}{n}" for p in ("COM", "LPT") for n in range(1, 10)}


def _unsafe_segment(seg: str) -> bool:
    if seg in ("", ".", ".."):
        return True
    if seg.endswith((".", " ")):
        return True
    return seg.split(".", 1)[0].upper() in _RESERVED


def validate_relpath(relpath: str) -> None:
    """Reject paths that could escape or misbehave on any receiving platform."""
    if (
        not relpath
        or relpath.startswith("/")
        or any(ch in _FORBIDDEN_CHARS or ord(ch) < 32 for ch in relpath)
        or any(_unsafe_segment(seg) for seg in relpath.split("/"))
    ):
        raise MailboxError(f"unsafe path in transfer: {relpath!r}")


@dataclass
class ManifestEntry:
    path: str
    size: int
    sha256: str


@dataclass
class Manifest:
    created: str
    sender: str
    files: list[ManifestEntry] = field(default_factory=list)
    version: int = 1

    @property
    def total_size(self) -> int:
        return sum(f.size for f in self.files)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)

    @classmethod
    def from_json(cls, text: str) -> Manifest:
        data = json.loads(text)
        return cls(
            created=data["created"],
            sender=data["sender"],
            files=[ManifestEntry(**f) for f in data["files"]],
            version=data.get("version", 1),
        )


class Mailbox(Protocol):
    def remote_path(self, transfer_id: str, relpath: str) -> str: ...
    async def allocate(self) -> str: ...
    async def put_file(
        self, transfer_id: str, relpath: str, local: Path, progress: ProgressFn | None = None
    ) -> None: ...
    async def write_manifest(self, transfer_id: str, manifest: Manifest) -> None: ...
    async def publish(self, transfer_id: str, words: int) -> str: ...
    async def resolve(self, code: str) -> str: ...
    async def claim(self, code: str, transfer_id: str) -> None: ...
    async def read_manifest(self, transfer_id: str) -> Manifest: ...
    async def get_file(
        self, transfer_id: str, relpath: str, local: Path, progress: ProgressFn | None = None
    ) -> None:
        """Download to exactly `local`; no temp file or rename (the caller owns that)."""
        ...

    async def is_claimed(self, transfer_id: str) -> bool: ...
    async def exists(self, transfer_id: str) -> bool: ...
    async def remove(self, transfer_id: str) -> None: ...
    async def mark_failed(self, transfer_id: str, message: str) -> None: ...
    async def failure(self, transfer_id: str) -> str | None: ...
