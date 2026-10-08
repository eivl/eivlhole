import asyncio
import hashlib
import json
import os

import pytest

from eivlhole import transfer
from eivlhole.errors import CodeNotFound, MailboxError, TransferError
from eivlhole.history import History
from eivlhole.mailbox.sftp import SftpMailbox
from eivlhole.ssh.client import sftp


@pytest.fixture
def drop(ssh_server):
    (ssh_server.root / "drop" / "codes").mkdir(parents=True)
    (ssh_server.root / "drop" / "transfers").mkdir()
    return ssh_server.root / "drop"


def test_collect_files_single_and_dir(tmp_path):
    (tmp_path / "a.txt").write_text("a")
    d = tmp_path / "pkg"
    (d / "sub").mkdir(parents=True)
    (d / "x.bin").write_bytes(b"x")
    (d / "sub" / "y.bin").write_bytes(b"y")
    got = transfer.collect_files([tmp_path / "a.txt", d])
    assert got == [
        (tmp_path / "a.txt", "a.txt"),
        (d / "sub" / "y.bin", "pkg/sub/y.bin"),
        (d / "x.bin", "pkg/x.bin"),
    ]


def test_collect_files_relpath_uses_forward_slash(tmp_path):
    d = tmp_path / "pkg"
    (d / "deep" / "er").mkdir(parents=True)
    (d / "deep" / "er" / "f").write_bytes(b"1")
    ((_, rel),) = transfer.collect_files([d])
    assert rel == "pkg/deep/er/f" and "\\" not in rel


def test_collect_files_missing_path(tmp_path):
    with pytest.raises(TransferError, match="no such file"):
        transfer.collect_files([tmp_path / "ghost"])


def test_collect_files_empty_dir(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(TransferError, match="empty"):
        transfer.collect_files([tmp_path / "empty"])


def test_collect_files_duplicate_names(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "a" / "same.txt").write_text("1")
    (tmp_path / "b" / "same.txt").write_text("2")
    with pytest.raises(TransferError, match="duplicate"):
        transfer.collect_files([tmp_path / "a" / "same.txt", tmp_path / "b" / "same.txt"])


def test_sha256_file(tmp_path):
    f = tmp_path / "f"
    f.write_bytes(b"abc" * 100000)
    assert transfer.sha256_file(f) == hashlib.sha256(b"abc" * 100000).hexdigest()


async def test_send_then_receive_end_to_end(target, ssh_server, drop, tmp_path):
    src = tmp_path / "src"
    (src / "pkg" / "sub").mkdir(parents=True)
    (src / "pkg" / "one.bin").write_bytes(b"1" * 200000)
    (src / "pkg" / "sub" / "two.txt").write_text("two")
    (src / "solo.txt").write_text("solo")
    hist = History(tmp_path / "h.sqlite")
    prog = []
    async with sftp(target, known_hosts=ssh_server.known_hosts) as s:
        mb = SftpMailbox(s, "/drop")
        result = await transfer.send(
            [src / "pkg", src / "solo.txt"],
            mb,
            history=hist,
            host_name="drop",
            words=3,
            progress=lambda n, d, t: prog.append(n),
        )
    assert len(result.files) == 3 and result.code.count("-") == 3
    assert {e.path for e in result.files} == {"pkg/one.bin", "pkg/sub/two.txt", "solo.txt"}
    assert (drop / "transfers" / result.transfer_id / "manifest.json").exists()
    assert "pkg/one.bin" in prog
    run = hist.run(result.run_id)
    assert run.kind == "send" and run.status == "ok" and run.host == "drop"
    sent = hist.files_for_run(result.run_id)
    assert {f.filename for f in sent} == {"one.bin", "two.txt", "solo.txt"}
    assert all(f.direction == "out" and f.status == "sent" for f in sent)

    out = tmp_path / "out"
    seen_manifest = []
    async with sftp(target, known_hosts=ssh_server.known_hosts) as s:
        mb = SftpMailbox(s, "/drop")
        r = await transfer.receive(
            result.code.upper().replace("-", " "),
            mb,
            out,
            history=hist,
            host_name="drop",
            confirm=lambda m: seen_manifest.append(m) or True,
        )
    assert seen_manifest[0].total_size == 200000 + 3 + 4
    assert (out / "pkg" / "one.bin").read_bytes() == b"1" * 200000
    assert (out / "pkg" / "sub" / "two.txt").read_text() == "two"
    assert (out / "solo.txt").read_text() == "solo"
    assert sorted(p.name for p in r.files) == ["one.bin", "solo.txt", "two.txt"]
    assert not (drop / "transfers" / result.transfer_id).exists()
    assert not (drop / "codes" / result.code).exists()
    rr = hist.run(r.run_id)
    assert rr.kind == "receive" and rr.status == "ok"
    got = hist.files_for_run(r.run_id)
    assert all(f.direction == "in" and f.status == "received" for f in got)
    hist.close()


async def test_receive_declined_leaves_transfer(target, ssh_server, drop, tmp_path):
    f = tmp_path / "f.txt"
    f.write_text("x")
    async with sftp(target, known_hosts=ssh_server.known_hosts) as s:
        mb = SftpMailbox(s, "/drop")
        res = await transfer.send([f], mb, history=None, host_name="drop")
        with pytest.raises(TransferError, match="cancelled"):
            await transfer.receive(
                res.code,
                mb,
                tmp_path / "out",
                history=None,
                host_name="drop",
                confirm=lambda m: False,
            )
        assert (drop / "codes" / res.code).exists()
        assert await mb.resolve(res.code) == res.transfer_id


async def test_receive_accepts_async_confirm(target, ssh_server, drop, tmp_path):
    f = tmp_path / "f.txt"
    f.write_text("x")
    async with sftp(target, known_hosts=ssh_server.known_hosts) as s:
        mb = SftpMailbox(s, "/drop")
        res = await transfer.send([f], mb, history=None, host_name="drop")

        async def yes(manifest):
            return True

        got = await transfer.receive(
            res.code, mb, tmp_path / "out", history=None, host_name="drop", confirm=yes
        )
        assert (tmp_path / "out" / "f.txt").read_text() == "x"
        assert got.files


async def test_receive_async_confirm_can_decline(target, ssh_server, drop, tmp_path):
    f = tmp_path / "f.txt"
    f.write_text("x")
    async with sftp(target, known_hosts=ssh_server.known_hosts) as s:
        mb = SftpMailbox(s, "/drop")
        res = await transfer.send([f], mb, history=None, host_name="drop")

        async def no(manifest):
            return False

        with pytest.raises(TransferError, match="cancelled"):
            await transfer.receive(
                res.code, mb, tmp_path / "out", history=None, host_name="drop", confirm=no
            )
        assert (drop / "codes" / res.code).exists()


async def test_receive_refuses_overwrite(target, ssh_server, drop, tmp_path):
    f = tmp_path / "f.txt"
    f.write_text("new")
    out = tmp_path / "out"
    out.mkdir()
    (out / "f.txt").write_text("old")
    async with sftp(target, known_hosts=ssh_server.known_hosts) as s:
        mb = SftpMailbox(s, "/drop")
        res = await transfer.send([f], mb, history=None, host_name="drop")
        with pytest.raises(TransferError, match="already exists"):
            await transfer.receive(res.code, mb, out, history=None, host_name="drop")
        assert (out / "f.txt").read_text() == "old"
        assert (drop / "codes" / res.code).exists()  # not claimed
        r = await transfer.receive(
            res.code, mb, out, history=None, host_name="drop", overwrite=True
        )
        assert (out / "f.txt").read_text() == "new" and r.transfer_id == res.transfer_id


async def test_receive_unknown_code(target, ssh_server, drop, tmp_path):
    async with sftp(target, known_hosts=ssh_server.known_hosts) as s:
        mb = SftpMailbox(s, "/drop")
        with pytest.raises(CodeNotFound):
            await transfer.receive("1-ghost-ghost", mb, tmp_path, history=None, host_name="drop")


async def test_receive_bad_code_format(target, ssh_server, drop, tmp_path):
    async with sftp(target, known_hosts=ssh_server.known_hosts) as s:
        mb = SftpMailbox(s, "/drop")
        with pytest.raises(TransferError, match="not a valid code"):
            await transfer.receive("hello", mb, tmp_path, history=None, host_name="drop")


async def test_receive_sha_mismatch_is_failed(target, ssh_server, drop, tmp_path):
    f = tmp_path / "f.txt"
    f.write_text("good")
    hist = History(tmp_path / "h.sqlite")
    async with sftp(target, known_hosts=ssh_server.known_hosts) as s:
        mb = SftpMailbox(s, "/drop")
        res = await transfer.send([f], mb, history=None, host_name="drop")
        (drop / "transfers" / res.transfer_id / "f.txt").write_text("tampered")
        with pytest.raises(TransferError, match="checksum"):
            await transfer.receive(res.code, mb, tmp_path / "out", history=hist, host_name="drop")
    assert not (tmp_path / "out" / "f.txt").exists()
    rec = hist.files()[0]
    assert rec.status == "failed" and "checksum" in rec.error
    hist.close()


async def test_two_receivers_one_code(target, ssh_server, drop, tmp_path):
    f = tmp_path / "f.txt"
    f.write_text("x")
    kh = ssh_server.known_hosts
    async with (
        sftp(target, known_hosts=kh) as s0,
        sftp(target, known_hosts=kh) as s1,
        sftp(target, known_hosts=kh) as s2,
    ):
        res = await transfer.send([f], SftpMailbox(s0, "/drop"), history=None, host_name="drop")
        results = await asyncio.gather(
            transfer.receive(
                res.code,
                SftpMailbox(s1, "/drop"),
                tmp_path / "o1",
                history=None,
                host_name="drop",
            ),
            transfer.receive(
                res.code,
                SftpMailbox(s2, "/drop"),
                tmp_path / "o2",
                history=None,
                host_name="drop",
            ),
            return_exceptions=True,
        )
    assert sum(isinstance(r, transfer.ReceiveResult) for r in results) == 1
    assert sum(isinstance(r, CodeNotFound) for r in results) == 1


async def test_wait_for_receipt(target, ssh_server, drop, tmp_path):
    f = tmp_path / "f.txt"
    f.write_text("x")
    states = []
    kh = ssh_server.known_hosts
    async with sftp(target, known_hosts=kh) as s1, sftp(target, known_hosts=kh) as s2:
        a, b = SftpMailbox(s1, "/drop"), SftpMailbox(s2, "/drop")
        res = await transfer.send([f], a, history=None, host_name="drop")

        async def receiver():
            await asyncio.sleep(0.2)
            await transfer.receive(res.code, b, tmp_path / "out", history=None, host_name="drop")

        await asyncio.gather(
            transfer.wait_for_receipt(a, res.transfer_id, poll=0.05, on_state=states.append),
            receiver(),
        )
    assert states == ["claimed", "received"]


async def test_send_missing_file_touches_nothing(target, ssh_server, drop, tmp_path):
    async with sftp(target, known_hosts=ssh_server.known_hosts) as s:
        mb = SftpMailbox(s, "/drop")
        with pytest.raises(TransferError):
            await transfer.send([tmp_path / "ghost"], mb, history=None, host_name="drop")
    assert list((drop / "transfers").iterdir()) == []


async def test_wait_for_receipt_failure_marker(target, ssh_server, drop, tmp_path):
    f = tmp_path / "f.txt"
    f.write_text("x")
    kh = ssh_server.known_hosts
    async with sftp(target, known_hosts=kh) as s1, sftp(target, known_hosts=kh) as s2:
        a, b = SftpMailbox(s1, "/drop"), SftpMailbox(s2, "/drop")
        res = await transfer.send([f], a, history=None, host_name="drop")
        await b.mark_failed(res.transfer_id, "f.txt: boom")
        with pytest.raises(TransferError, match="receiver reported failure"):
            await transfer.wait_for_receipt(a, res.transfer_id, poll=0.05)


async def test_wait_for_receipt_vanished_counts_as_received(target, ssh_server, drop, tmp_path):
    f = tmp_path / "f.txt"
    f.write_text("x")
    states = []
    kh = ssh_server.known_hosts
    async with sftp(target, known_hosts=kh) as s1, sftp(target, known_hosts=kh) as s2:
        a, b = SftpMailbox(s1, "/drop"), SftpMailbox(s2, "/drop")
        res = await transfer.send([f], a, history=None, host_name="drop")
        await b.remove(res.transfer_id)
        await transfer.wait_for_receipt(a, res.transfer_id, poll=0.05, on_state=states.append)
    assert states[-1] == "received"


async def test_receive_rejects_traversal_manifest(target, ssh_server, drop, tmp_path):
    f = tmp_path / "f.txt"
    f.write_text("x")
    async with sftp(target, known_hosts=ssh_server.known_hosts) as s:
        mb = SftpMailbox(s, "/drop")
        res = await transfer.send([f], mb, history=None, host_name="drop")
        manifest = drop / "transfers" / res.transfer_id / "manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "created": "c",
                    "sender": "s",
                    "version": 1,
                    "files": [{"path": "../escape.txt", "size": 1, "sha256": "0" * 64}],
                }
            )
        )
        out = tmp_path / "out"
        with pytest.raises(MailboxError):
            await transfer.receive(res.code, mb, out, history=None, host_name="drop")
        assert not (tmp_path / "escape.txt").exists()
        assert await mb.resolve(res.code) == res.transfer_id


async def test_receive_overwrite_keeps_original_on_mismatch(target, ssh_server, drop, tmp_path):
    f = tmp_path / "f.txt"
    f.write_text("new")
    out = tmp_path / "out"
    out.mkdir()
    (out / "f.txt").write_text("old")
    async with sftp(target, known_hosts=ssh_server.known_hosts) as s:
        mb = SftpMailbox(s, "/drop")
        res = await transfer.send([f], mb, history=None, host_name="drop")
        (drop / "transfers" / res.transfer_id / "f.txt").write_text("tampered")
        with pytest.raises(TransferError, match="checksum"):
            await transfer.receive(
                res.code, mb, out, history=None, host_name="drop", overwrite=True
            )
    assert (out / "f.txt").read_text() == "old"
    assert not (out / "f.txt.part").exists()


async def test_send_cleans_up_when_publish_fails(target, ssh_server, drop, tmp_path, monkeypatch):
    f = tmp_path / "f.txt"
    f.write_text("x")
    hist = History(tmp_path / "h.sqlite")
    async with sftp(target, known_hosts=ssh_server.known_hosts) as s:
        mb = SftpMailbox(s, "/drop")

        async def boom(*args, **kwargs):
            raise MailboxError("boom")

        monkeypatch.setattr(mb, "publish", boom)
        with pytest.raises(MailboxError, match="boom"):
            await transfer.send([f], mb, history=hist, host_name="drop")
    assert list((drop / "transfers").iterdir()) == []
    assert hist.run(1).status == "failed"
    hist.close()


@pytest.mark.skipif(os.name != "posix", reason="symlinks")
async def test_receive_rejects_path_resolving_outside_out_dir(target, ssh_server, drop, tmp_path):
    src = tmp_path / "link"
    src.mkdir()
    (src / "x.txt").write_text("x")
    outside = tmp_path / "outside"
    outside.mkdir()
    out = tmp_path / "out"
    out.mkdir()
    (out / "link").symlink_to(outside, target_is_directory=True)
    async with sftp(target, known_hosts=ssh_server.known_hosts) as s:
        mb = SftpMailbox(s, "/drop")
        res = await transfer.send([src], mb, history=None, host_name="drop")
        with pytest.raises(MailboxError, match="unsafe path"):
            await transfer.receive(res.code, mb, out, history=None, host_name="drop")
        assert await mb.resolve(res.code) == res.transfer_id
    assert list(outside.iterdir()) == []


async def test_receive_cancelled_marks_failed(target, ssh_server, drop, tmp_path, monkeypatch):
    f = tmp_path / "f.txt"
    f.write_text("x")
    out = tmp_path / "out"
    async with sftp(target, known_hosts=ssh_server.known_hosts) as s:
        mb = SftpMailbox(s, "/drop")
        res = await transfer.send([f], mb, history=None, host_name="drop")

        async def interrupted(transfer_id, relpath, local, progress=None):
            local.parent.mkdir(parents=True, exist_ok=True)
            local.write_bytes(b"partial")
            raise asyncio.CancelledError

        monkeypatch.setattr(mb, "get_file", interrupted)
        with pytest.raises(asyncio.CancelledError):
            await transfer.receive(res.code, mb, out, history=None, host_name="drop")
        msg = await mb.failure(res.transfer_id)
    assert msg is not None and "interrupted" in msg
    assert not (out / "f.txt.part").exists()
    assert not (out / "f.txt").exists()
