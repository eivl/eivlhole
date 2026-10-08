import asyncio
import json
import re
from pathlib import Path

import asyncssh
import pytest

from eivlhole.errors import CodeNotFound, MailboxError
from eivlhole.mailbox.base import Manifest, ManifestEntry
from eivlhole.mailbox.sftp import SftpMailbox
from eivlhole.ssh.client import sftp


def layout(root: Path) -> Path:
    (root / "drop" / "codes").mkdir(parents=True)
    (root / "drop" / "transfers").mkdir()
    return root / "drop"


@pytest.fixture
def drop(ssh_server):
    return layout(ssh_server.root)


async def test_allocate_put_manifest_publish(target, ssh_server, drop, tmp_path):
    local = tmp_path / "hello.txt"
    local.write_bytes(b"hello " * 1000)
    seen = []
    async with sftp(target, known_hosts=ssh_server.known_hosts) as s:
        mb = SftpMailbox(s, "/drop")
        tid = await mb.allocate()
        assert re.fullmatch(r"[0-9a-f]{32}", tid)
        assert (drop / "transfers" / tid).is_dir()
        await mb.put_file(
            tid, "sub/hello.txt", local, progress=lambda n, d, t: seen.append((n, d, t))
        )
        assert (drop / "transfers" / tid / "sub" / "hello.txt").read_bytes() == local.read_bytes()
        assert not list((drop / "transfers" / tid / "sub").glob("*.part"))
        assert seen[-1] == ("sub/hello.txt", 6000, 6000)
        assert mb.remote_path(tid, "sub/hello.txt") == f"/drop/transfers/{tid}/sub/hello.txt"
        m = Manifest(
            created="2026-10-08T07:00:00+00:00",
            sender="me@box",
            files=[ManifestEntry("sub/hello.txt", 6000, "abc")],
        )
        await mb.write_manifest(tid, m)
        written = json.loads((drop / "transfers" / tid / "manifest.json").read_text())
        assert written["files"][0]["path"] == "sub/hello.txt"
        code = await mb.publish(tid, 3)
        assert (drop / "codes" / code).read_text().strip() == tid
        assert len(code.split("-")) == 4


async def test_resolve_claim_read_get_remove(target, ssh_server, drop, tmp_path):
    local = tmp_path / "a.bin"
    local.write_bytes(bytes(range(256)) * 100)
    async with sftp(target, known_hosts=ssh_server.known_hosts) as s:
        mb = SftpMailbox(s, "/drop")
        tid = await mb.allocate()
        await mb.put_file(tid, "a.bin", local)
        manifest = Manifest(created="c", sender="s", files=[ManifestEntry("a.bin", 25600, "x")])
        await mb.write_manifest(tid, manifest)
        code = await mb.publish(tid, 2)
        assert await mb.resolve(code) == tid
        assert await mb.resolve(code) == tid  # resolve does not consume
        assert await mb.is_claimed(tid) is False
        await mb.claim(code, tid)
        assert await mb.is_claimed(tid) is True
        assert not (drop / "codes" / code).exists()
        with pytest.raises(CodeNotFound):
            await mb.resolve(code)
        with pytest.raises(CodeNotFound):
            await mb.claim(code, tid)
        m = await mb.read_manifest(tid)
        assert m.files[0].path == "a.bin" and m.total_size == 25600
        out = tmp_path / "out" / "a.bin"
        await mb.get_file(tid, "a.bin", out)
        assert out.read_bytes() == local.read_bytes()
        assert await mb.exists(tid) is True
        await mb.remove(tid)
        assert await mb.exists(tid) is False
        await mb.remove(tid)  # idempotent


async def test_resolve_unknown_code(target, ssh_server, drop):
    async with sftp(target, known_hosts=ssh_server.known_hosts) as s:
        mb = SftpMailbox(s, "/drop")
        with pytest.raises(CodeNotFound, match="not found"):
            await mb.resolve("1-nope-nope")


async def test_resolve_rejects_garbage_transfer_id(target, ssh_server, drop):
    (drop / "codes" / "1-bad").write_text("../../etc\n")
    async with sftp(target, known_hosts=ssh_server.known_hosts) as s:
        mb = SftpMailbox(s, "/drop")
        with pytest.raises(MailboxError, match="malformed"):
            await mb.resolve("1-bad")


async def test_publish_retries_on_collision(target, ssh_server, drop, monkeypatch):
    from eivlhole.mailbox import sftp as sftp_mod

    calls = iter(["1-same-same", "1-same-same", "2-other-other"])
    monkeypatch.setattr(sftp_mod, "generate_code", lambda words: next(calls))
    (drop / "codes" / "1-same-same").write_text("taken\n")
    async with sftp(target, known_hosts=ssh_server.known_hosts) as s:
        mb = SftpMailbox(s, "/drop")
        tid = await mb.allocate()
        assert await mb.publish(tid, 2) == "2-other-other"


async def test_allocate_without_layout_is_mailbox_error(target, ssh_server):
    async with sftp(target, known_hosts=ssh_server.known_hosts) as s:
        mb = SftpMailbox(s, "/missing")
        with pytest.raises(MailboxError, match="server init"):
            await mb.allocate()


async def test_concurrent_claims_only_one_wins(target, ssh_server, drop):
    kh = ssh_server.known_hosts
    async with sftp(target, known_hosts=kh) as s1, sftp(target, known_hosts=kh) as s2:
        a, b = SftpMailbox(s1, "/drop"), SftpMailbox(s2, "/drop")
        tid = await a.allocate()
        code = await a.publish(tid, 2)
        results = await asyncio.gather(
            a.claim(code, tid), b.claim(code, tid), return_exceptions=True
        )
        assert sum(r is None for r in results) == 1
        assert sum(isinstance(r, CodeNotFound) for r in results) == 1


@pytest.mark.parametrize("bad", ["../x", "/etc/passwd", "a/../b", "a\\b", ""])
async def test_remote_path_rejects_traversal(target, ssh_server, drop, bad):
    async with sftp(target, known_hosts=ssh_server.known_hosts) as s:
        mb = SftpMailbox(s, "/drop")
        with pytest.raises(MailboxError):
            mb.remote_path("0" * 32, bad)


async def test_claim_rejects_bad_code_and_id(target, ssh_server, drop):
    async with sftp(target, known_hosts=ssh_server.known_hosts) as s:
        mb = SftpMailbox(s, "/drop")
        tid = await mb.allocate()
        code = await mb.publish(tid, 2)
        with pytest.raises(MailboxError):
            await mb.claim("../x", tid)
        with pytest.raises(MailboxError):
            await mb.claim(code, "zzz")


async def test_resolve_without_layout_is_mailbox_error(target, ssh_server):
    async with sftp(target, known_hosts=ssh_server.known_hosts) as s:
        mb = SftpMailbox(s, "/missing")
        with pytest.raises(MailboxError, match="server init"):
            await mb.resolve("1-a-b")


async def test_read_manifest_corrupt(target, ssh_server, drop):
    async with sftp(target, known_hosts=ssh_server.known_hosts) as s:
        mb = SftpMailbox(s, "/drop")
        tid = await mb.allocate()
        (drop / "transfers" / tid / "manifest.json").write_text('{"nope": 1}')
        with pytest.raises(MailboxError, match="corrupt"):
            await mb.read_manifest(tid)


async def test_mark_failed_and_failure(target, ssh_server, drop):
    async with sftp(target, known_hosts=ssh_server.known_hosts) as s:
        mb = SftpMailbox(s, "/drop")
        tid = await mb.allocate()
        assert await mb.failure(tid) is None
        await mb.mark_failed(tid, "a.txt: checksum mismatch")
        assert await mb.failure(tid) == "a.txt: checksum mismatch"


@pytest.mark.parametrize(
    "text",
    [
        '{"created":"c","sender":"s","files":[{"path":1,"size":1,"sha256":"x"}]}',
        '{"created":"c","sender":"s","files":[{"path":"a","size":"1","sha256":"x"}]}',
        '{"created":"c","sender":"s","files":[{"path":"a","size":1,"sha256":null}]}',
        '{"created":"c","sender":"s","files":[{"path":"a","size":true,"sha256":"x"}]}',
        '{"created":"c","sender":"s","files":["a"]}',
        '{"created":"c","sender":"s","files":{"a":1}}',
        "[]",
    ],
)
def test_manifest_from_json_type_checks(text):
    with pytest.raises(ValueError):
        Manifest.from_json(text)


async def test_put_file_makedirs_error_is_mailbox_error(
    target, ssh_server, drop, tmp_path, monkeypatch
):
    local = tmp_path / "x.txt"
    local.write_text("x")
    async with sftp(target, known_hosts=ssh_server.known_hosts) as s:
        mb = SftpMailbox(s, "/drop")
        tid = await mb.allocate()

        async def denied(*args, **kwargs):
            raise asyncssh.SFTPPermissionDenied("denied")

        monkeypatch.setattr(s, "makedirs", denied)
        with pytest.raises(MailboxError, match="upload of sub/x.txt failed"):
            await mb.put_file(tid, "sub/x.txt", local)
