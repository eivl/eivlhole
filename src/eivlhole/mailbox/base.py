"""Mailbox protocol: how codes map to transfers on the drop host."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Protocol

ProgressFn = Callable[[str, int, int], None]


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
    ) -> None: ...
    async def is_claimed(self, transfer_id: str) -> bool: ...
    async def exists(self, transfer_id: str) -> bool: ...
    async def remove(self, transfer_id: str) -> None: ...
