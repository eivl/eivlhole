import os
import re
from datetime import date

import asyncssh
import pytest

from eivlhole.config import Config, Defaults, HolidayConfig, HostConfig, JobConfig, PatternConfig
from eivlhole.errors import ConfigError
from eivlhole.history import History
from eivlhole.jobs import runner

WEEKDAY = date(2026, 10, 8)  # Thursday, no holiday


def make_config(ssh_server, client_key, job: JobConfig, port: int | None = None) -> Config:
    host = HostConfig(
        name="drop",
        hostname=ssh_server.host,
        user=ssh_server.user,
        port=port or ssh_server.port,
        key=client_key.as_posix(),
    )
    return Config(defaults=Defaults(host="drop"), hosts={"drop": host}, jobs={job.name: job})


@pytest.fixture
def incoming(ssh_server):
    d = ssh_server.root / "incoming"
    d.mkdir()
    return d


def no_sleep_factory():
    calls = []

    async def fake_sleep(s):
        calls.append(s)

    return fake_sleep, calls


async def test_run_job_happy_path(ssh_server, client_key, incoming, tmp_path):
    src = tmp_path / "out"
    src.mkdir()
    (src / "daily-20261008.csv").write_text("a,b")
    (src / "summary.xlsx").write_text("xlsx")
    job = JobConfig(
        name="reports",
        remote_dir="/incoming",
        files=[str(src / "summary.xlsx")],
        patterns=[PatternConfig(dir=str(src), regex=r"daily-\d{8}\.csv")],
    )
    cfg = make_config(ssh_server, client_key, job)
    hist = History(tmp_path / "h.sqlite")
    result = await runner.run_job(
        "reports", cfg, history=hist, today=WEEKDAY, known_hosts=ssh_server.known_hosts
    )
    assert result.status == "ok" and result.exit_code == 0
    assert sorted(o.local.name for o in result.sent) == ["daily-20261008.csv", "summary.xlsx"]
    assert (incoming / "daily-20261008.csv").read_text() == "a,b"
    assert not list(incoming.glob("*.part"))
    run = hist.run(result.run_id)
    assert run.kind == "job" and run.job == "reports" and run.host == "drop" and run.status == "ok"
    rows = hist.files_for_run(result.run_id)
    assert {r.filename for r in rows} == {"daily-20261008.csv", "summary.xlsx"}
    assert all(r.direction == "out" and r.status == "sent" and r.sha256 for r in rows)
    assert rows[0].remote_path.startswith("/incoming/")
    hist.close()


async def test_run_job_overwrites_existing_remote(ssh_server, client_key, incoming, tmp_path):
    (incoming / "daily.csv").write_text("yesterday")
    f = tmp_path / "daily.csv"
    f.write_text("today")
    job = JobConfig(name="j", remote_dir="/incoming", files=[str(f)])
    cfg = make_config(ssh_server, client_key, job)
    result = await runner.run_job(
        "j", cfg, history=None, today=WEEKDAY, known_hosts=ssh_server.known_hosts
    )
    assert result.exit_code == 0 and (incoming / "daily.csv").read_text() == "today"


async def test_run_job_creates_remote_dir(ssh_server, client_key, incoming, tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("x")
    job = JobConfig(name="j", remote_dir="/incoming/new/deep", files=[str(f)])
    cfg = make_config(ssh_server, client_key, job)
    result = await runner.run_job(
        "j", cfg, history=None, today=WEEKDAY, known_hosts=ssh_server.known_hosts
    )
    assert result.exit_code == 0 and (incoming / "new" / "deep" / "x.txt").exists()


async def test_run_job_skips_on_holiday_and_weekend(ssh_server, client_key, incoming, tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("x")
    job = JobConfig(
        name="j",
        remote_dir="/incoming",
        files=[str(f)],
        holidays=HolidayConfig(country="NO", extra=["2026-12-24"]),
    )
    cfg = make_config(ssh_server, client_key, job)
    hist = History(tmp_path / "h.sqlite")
    for day, reason in [
        (date(2026, 4, 3), "Good Friday"),
        (date(2026, 10, 10), "weekend (Saturday)"),
        (date(2026, 12, 24), "extra: 2026-12-24"),
    ]:
        result = await runner.run_job(
            "j", cfg, history=hist, today=day, known_hosts=ssh_server.known_hosts
        )
        assert result.status == "skipped" and result.exit_code == 2 and result.reason == reason
        assert (
            hist.run(result.run_id).status == "skipped" and hist.run(result.run_id).exit_code == 2
        )
    assert not (incoming / "x.txt").exists()
    forced = await runner.run_job(
        "j",
        cfg,
        history=hist,
        today=date(2026, 4, 3),
        force=True,
        known_hosts=ssh_server.known_hosts,
    )
    assert forced.exit_code == 0 and (incoming / "x.txt").exists()
    hist.close()


async def test_run_job_dry_run_touches_nothing(ssh_server, client_key, incoming, tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("x")
    job = JobConfig(name="j", remote_dir="/incoming", files=[str(f), str(tmp_path / "ghost")])
    cfg = make_config(ssh_server, client_key, job, port=1)  # unreachable: must not connect
    hist = History(tmp_path / "h.sqlite")
    result = await runner.run_job("j", cfg, history=hist, today=WEEKDAY, dry_run=True)
    assert result.status == "dry-run" and result.exit_code == 0
    assert [o.status for o in result.outcomes] == ["pending", "failed"]
    assert hist.runs() == []
    hist.close()


async def test_run_job_missing_file_is_partial(ssh_server, client_key, incoming, tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("x")
    job = JobConfig(name="j", remote_dir="/incoming", files=[str(f), str(tmp_path / "ghost.txt")])
    cfg = make_config(ssh_server, client_key, job)
    hist = History(tmp_path / "h.sqlite")
    result = await runner.run_job(
        "j", cfg, history=hist, today=WEEKDAY, known_hosts=ssh_server.known_hosts
    )
    assert result.status == "partial" and result.exit_code == 3
    assert [o.status for o in result.outcomes] == ["sent", "failed"]
    rows = {r.filename: r for r in hist.files_for_run(result.run_id)}
    assert rows["ghost.txt"].status == "failed" and "no such file" in rows["ghost.txt"].error
    assert hist.run(result.run_id).exit_code == 3
    hist.close()


async def test_run_job_connect_failure_marks_all_failed(ssh_server, client_key, tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("x")
    job = JobConfig(name="j", remote_dir="/incoming", files=[str(f)])
    cfg = make_config(ssh_server, client_key, job, port=1)
    hist = History(tmp_path / "h.sqlite")
    sleep, _ = no_sleep_factory()
    result = await runner.run_job(
        "j", cfg, history=hist, today=WEEKDAY, known_hosts=ssh_server.known_hosts, sleep=sleep
    )
    assert result.status == "failed" and result.exit_code == 1
    assert result.outcomes[0].status == "failed" and "cannot connect" in result.outcomes[0].error
    run = hist.run(result.run_id)
    assert run.status == "failed" and "cannot connect" in run.error
    hist.close()


async def test_run_job_untrusted_host_never_prompts(ssh_server, client_key, incoming, tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("x")
    job = JobConfig(name="j", remote_dir="/incoming", files=[str(f)])
    cfg = make_config(ssh_server, client_key, job)
    result = await runner.run_job(
        "j", cfg, history=None, today=WEEKDAY, known_hosts=tmp_path / "empty_kh"
    )
    assert result.exit_code == 1 and "hosts trust" in result.reason


async def test_run_job_no_files_selected(ssh_server, client_key, tmp_path):
    job = JobConfig(
        name="j",
        remote_dir="/incoming",
        patterns=[PatternConfig(dir=str(tmp_path), regex=r"nothing")],
    )
    cfg = make_config(ssh_server, client_key, job)
    result = await runner.run_job(
        "j", cfg, history=None, today=WEEKDAY, known_hosts=ssh_server.known_hosts
    )
    assert result.status == "failed" and result.exit_code == 1 and "no files" in result.reason
    assert any("matched no files" in w for w in result.warnings)


async def test_run_job_unknown_job(ssh_server, client_key):
    cfg = make_config(ssh_server, client_key, JobConfig(name="j", remote_dir="/x"))
    with pytest.raises(ConfigError, match="unknown job 'nope'"):
        await runner.run_job("nope", cfg, history=None)


@pytest.mark.parametrize("policy", ["keep", "move", "delete"])
async def test_on_success_policies(ssh_server, client_key, incoming, tmp_path, policy):
    src = tmp_path / "out"
    src.mkdir()
    f = src / "x.txt"
    f.write_text("x")
    (src / "sent").mkdir()
    (src / "sent" / "x.txt").write_text("older copy")
    job = JobConfig(name="j", remote_dir="/incoming", files=[str(f)], on_success=policy)
    cfg = make_config(ssh_server, client_key, job)
    result = await runner.run_job(
        "j", cfg, history=None, today=WEEKDAY, known_hosts=ssh_server.known_hosts
    )
    assert result.exit_code == 0
    if policy == "keep":
        assert f.exists()
    elif policy == "move":
        assert not f.exists() and (src / "sent" / "x.txt").read_text() == "x"
    else:
        assert not f.exists()


class FakeSftp:
    def __init__(self, fail_times: int, unsupported: bool = False):
        self.fail_times = fail_times
        self.unsupported = unsupported
        self.calls: list[tuple[str, ...]] = []
        self.removed: list[str] = []

    async def put(self, local, remote, *, block_size=0, progress_handler=None):
        self.calls.append(("put", remote))
        if self.fail_times > 0:
            self.fail_times -= 1
            raise OSError("flaky network")

    async def posix_rename(self, a, b):
        if self.unsupported:
            raise asyncssh.SFTPOpUnsupported("no posix-rename")
        self.calls.append(("posix_rename", a, b))

    async def remove(self, p):
        self.removed.append(p)

    async def rename(self, a, b):
        self.calls.append(("rename", a, b))


async def test_upload_file_retries_with_backoff(tmp_path):
    f = tmp_path / "f"
    f.write_text("x")
    sleep, calls = no_sleep_factory()
    s = FakeSftp(fail_times=2)
    attempts = await runner.upload_file(s, f, "/in/f", retries=3, sleep=sleep)
    assert attempts == 3 and calls == [1.0, 2.0]
    op, part, final = s.calls[-1]
    assert op == "posix_rename" and final == "/in/f"
    assert re.fullmatch(r"/in/f\.[0-9a-f]{8}\.part", part)
    assert s.calls[-2] == ("put", part)


async def test_upload_file_gives_up_after_retries(tmp_path):
    f = tmp_path / "f"
    f.write_text("x")
    sleep, calls = no_sleep_factory()
    s = FakeSftp(fail_times=5)
    with pytest.raises(OSError, match="flaky"):
        await runner.upload_file(s, f, "/in/f", retries=2, sleep=sleep)
    assert calls == [1.0, 2.0]
    part = s.calls[-1][1]
    assert re.fullmatch(r"/in/f\.[0-9a-f]{8}\.part", part)
    assert s.removed == [part]


async def test_replace_remote_falls_back_without_posix_rename():
    s = FakeSftp(fail_times=0, unsupported=True)
    await runner.replace_remote(s, "/in/f.part", "/in/f")
    assert s.removed == ["/in/f"] and s.calls[-1] == ("rename", "/in/f.part", "/in/f")


def test_apply_on_success_move_creates_sent_dir(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("a")
    runner.apply_on_success("move", f)
    assert (tmp_path / "sent" / "a.txt").read_text() == "a" and not f.exists()


posix_only = pytest.mark.skipif(
    os.name != "posix" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="needs POSIX permissions and a non-root user",
)


@posix_only
async def test_run_job_remote_dir_not_writable(ssh_server, client_key, incoming, tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("x")
    job = JobConfig(name="j", remote_dir="/incoming/sub", files=[str(f)])
    cfg = make_config(ssh_server, client_key, job)
    hist = History(tmp_path / "h.sqlite")
    incoming.chmod(0o500)
    try:
        result = await runner.run_job(
            "j", cfg, history=hist, today=WEEKDAY, known_hosts=ssh_server.known_hosts
        )
    finally:
        incoming.chmod(0o700)
    assert result.status == "failed" and result.exit_code == 1
    assert len(result.outcomes) == 1
    assert result.outcomes[0].status == "failed" and result.outcomes[0].error
    run = hist.run(result.run_id)
    assert run.exit_code == 1 and run.error
    rows = hist.files_for_run(result.run_id)
    assert len(rows) == 1 and rows[0].status == "failed"
    hist.close()


@posix_only
async def test_on_success_failure_keeps_sent(ssh_server, client_key, incoming, tmp_path):
    src = tmp_path / "out"
    src.mkdir()
    f = src / "x.txt"
    f.write_text("x")
    (src / "sent").mkdir()
    job = JobConfig(name="j", remote_dir="/incoming", files=[str(f)], on_success="move")
    cfg = make_config(ssh_server, client_key, job)
    (src / "sent").chmod(0o500)
    try:
        result = await runner.run_job(
            "j", cfg, history=None, today=WEEKDAY, known_hosts=ssh_server.known_hosts
        )
    finally:
        (src / "sent").chmod(0o700)
    assert (incoming / "x.txt").read_text() == "x"
    o = result.outcomes[0]
    assert o.status == "sent" and "on_success" in o.error
    assert result.exit_code == 0 and result.warnings


async def test_run_job_duplicate_basenames(ssh_server, client_key, incoming, tmp_path):
    for d, content in (("a", "first"), ("b", "second")):
        (tmp_path / d).mkdir()
        (tmp_path / d / "x.txt").write_text(content)
    job = JobConfig(
        name="j",
        remote_dir="/incoming",
        files=[str(tmp_path / "a" / "x.txt"), str(tmp_path / "b" / "x.txt")],
    )
    cfg = make_config(ssh_server, client_key, job)
    result = await runner.run_job(
        "j", cfg, history=None, today=WEEKDAY, known_hosts=ssh_server.known_hosts
    )
    assert [o.status for o in result.outcomes] == ["sent", "failed"]
    assert "duplicate remote name" in result.outcomes[1].error
    assert result.exit_code == 3
    assert (incoming / "x.txt").read_text() == "first"


async def test_run_job_writes_file_rows_before_on_success(
    ssh_server, client_key, incoming, tmp_path, monkeypatch
):
    f = tmp_path / "x.txt"
    f.write_text("x")
    job = JobConfig(name="j", remote_dir="/incoming", files=[str(f)], on_success="move")
    cfg = make_config(ssh_server, client_key, job)
    hist = History(tmp_path / "h.sqlite")
    rows_at_on_success = []

    def locked(policy, local):
        rows_at_on_success.extend(hist.files(["x.txt"]))
        raise OSError("locked")

    monkeypatch.setattr(runner, "apply_on_success", locked)
    result = await runner.run_job(
        "j", cfg, history=hist, today=WEEKDAY, known_hosts=ssh_server.known_hosts
    )
    assert [r.status for r in rows_at_on_success] == ["sent"]
    rows = hist.files_for_run(result.run_id)
    assert len(rows) == 1 and rows[0].status == "sent" and "on_success" in rows[0].error
    assert result.exit_code == 0 and f.exists()
    hist.close()


async def test_run_job_file_changed_during_upload(
    ssh_server, client_key, incoming, tmp_path, monkeypatch
):
    f = tmp_path / "x.txt"
    f.write_text("x")
    job = JobConfig(name="j", remote_dir="/incoming", files=[str(f)], on_success="delete")
    cfg = make_config(ssh_server, client_key, job)

    async def growing_upload(sftp_client, local, remote, **kwargs):
        with local.open("a") as fh:
            fh.write("more")
        return 1

    monkeypatch.setattr(runner, "upload_file", growing_upload)
    hist = History(tmp_path / "h.sqlite")
    result = await runner.run_job(
        "j", cfg, history=hist, today=WEEKDAY, known_hosts=ssh_server.known_hosts
    )
    o = result.outcomes[0]
    assert o.status == "failed" and "changed while uploading" in o.error
    assert result.exit_code == 1 and f.read_text() == "xmore"
    rows = hist.files_for_run(result.run_id)
    assert len(rows) == 1 and rows[0].status == "failed"
    hist.close()


async def test_run_job_retries_hash_and_upload_per_attempt(
    ssh_server, client_key, incoming, tmp_path, monkeypatch
):
    f = tmp_path / "x.txt"
    f.write_text("x")
    job = JobConfig(name="j", remote_dir="/incoming", files=[str(f)], retries=2)
    cfg = make_config(ssh_server, client_key, job)
    seen = []

    async def flaky_upload(sftp_client, local, remote, **kwargs):
        seen.append(kwargs["retries"])
        if len(seen) < 3:
            raise OSError("flaky network")
        return 1

    sleep, calls = no_sleep_factory()
    monkeypatch.setattr(runner, "upload_file", flaky_upload)
    result = await runner.run_job(
        "j", cfg, history=None, today=WEEKDAY, known_hosts=ssh_server.known_hosts, sleep=sleep
    )
    assert result.exit_code == 0 and result.outcomes[0].attempts == 3
    assert seen == [0, 0, 0] and calls == [1.0, 2.0]


async def test_run_job_retries_connect_failure(ssh_server, client_key, tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("x")
    job = JobConfig(name="j", remote_dir="/incoming", files=[str(f)])
    cfg = make_config(ssh_server, client_key, job, port=1)
    sleep, calls = no_sleep_factory()
    result = await runner.run_job(
        "j", cfg, history=None, today=WEEKDAY, known_hosts=ssh_server.known_hosts, sleep=sleep
    )
    assert calls == [10.0, 30.0, 60.0]
    assert result.exit_code == 1 and "after 4 attempts" in result.reason
    assert "after 4 attempts" in result.outcomes[0].error


async def test_run_job_host_key_error_not_retried(ssh_server, client_key, incoming, tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("x")
    job = JobConfig(name="j", remote_dir="/incoming", files=[str(f)])
    cfg = make_config(ssh_server, client_key, job)
    sleep, calls = no_sleep_factory()
    result = await runner.run_job(
        "j", cfg, history=None, today=WEEKDAY, known_hosts=tmp_path / "empty_kh", sleep=sleep
    )
    assert calls == [] and result.exit_code == 1 and "hosts trust" in result.reason
