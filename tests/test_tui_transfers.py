import asyncio
import re
import shutil

from textual.widgets import RichLog

from eivlhole import paths
from eivlhole.jobs import runner
from eivlhole.tui import app as app_module
from eivlhole.tui.app import EivlholeApp
from eivlhole.tui.panels import HostsPanel
from eivlhole.tui.screens import ConfirmScreen
from tests.test_tui_actions import submit_prompt
from tests.test_tui_app import status

CODE_RE = re.compile(r"\b\d{1,3}(?:-[a-z]+){3}\b")


def freeze_today(monkeypatch):
    """Pin the runner's calendar to a plain Thursday so weekends/holidays never skip the job."""
    from datetime import date

    class FixedDate(date):
        @classmethod
        def today(cls):
            return date(2026, 10, 8)

    monkeypatch.setattr(runner, "date", FixedDate)


def write_full_config(ssh_server, client_key, tmp_path, *, with_job=True):
    (ssh_server.root / "drop" / "codes").mkdir(parents=True)
    (ssh_server.root / "drop" / "transfers").mkdir()
    (ssh_server.root / "incoming").mkdir()
    src = tmp_path / "out"
    src.mkdir()
    (src / "summary.xlsx").write_text("xlsx")
    inbox = tmp_path / "inbox"
    text = (
        "[defaults]\n"
        'host = "drop"\n'
        'drop_root = "/drop"\n'
        f'receive_dir = "{inbox.as_posix()}"\n\n'
        "[hosts.drop]\n"
        f'hostname = "{ssh_server.host}"\n'
        f"port = {ssh_server.port}\n"
        f'user = "{ssh_server.user}"\n'
        f'key = "{client_key.as_posix()}"\n'
    )
    if with_job:
        text += (
            "\n[jobs.reports]\n"
            'remote_dir = "/incoming"\n'
            f'files = ["{(src / "summary.xlsx").as_posix()}"]\n'
            'schedule = { time = "07:00", days = ["tue", "thu"] }\n'
        )
    paths.config_file().write_text(text)
    shutil.copy(ssh_server.known_hosts, paths.known_hosts_file())
    return src, inbox


def log_text(app) -> str:
    return "\n".join(
        str(line.text) if hasattr(line, "text") else str(line)
        for line in app.query_one("#log", RichLog).lines
    )


async def wait_for(pilot, predicate, timeout=10.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        await pilot.pause(0.05)
        if predicate():
            return
    raise AssertionError("condition not met in time")


async def test_run_job_from_tui(config_dir, ssh_server, client_key, tmp_path, monkeypatch):
    freeze_today(monkeypatch)
    write_full_config(ssh_server, client_key, tmp_path)
    app = EivlholeApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("r")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert (ssh_server.root / "incoming" / "summary.xlsx").read_text() == "xlsx"
        assert "ok" in status(app) and "1 sent" in status(app)
        assert "summary.xlsx" in log_text(app)
        assert app.query_one("#history").row_count == 1


async def test_dry_run_from_tui(config_dir, ssh_server, client_key, tmp_path, monkeypatch):
    freeze_today(monkeypatch)
    write_full_config(ssh_server, client_key, tmp_path)
    app = EivlholeApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("d")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert "would send 1 file" in log_text(app)
        assert not (ssh_server.root / "incoming" / "summary.xlsx").exists()


async def test_run_without_jobs_shows_message(config_dir, ssh_server, client_key, tmp_path):
    write_full_config(ssh_server, client_key, tmp_path, with_job=False)
    app = EivlholeApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("r")
        await pilot.press("d")
        await pilot.pause()
        assert "no job selected" in status(app)


async def test_send_and_receive_round_trip(config_dir, ssh_server, client_key, tmp_path):
    src, inbox = write_full_config(ssh_server, client_key, tmp_path)
    f = tmp_path / "report.txt"
    f.write_text("quarterly")
    app = EivlholeApp()
    app.poll = 0.1
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("s")
        await submit_prompt(app, pilot, str(f))
        await wait_for(pilot, lambda: "Code:" in log_text(app))
        code = CODE_RE.search(log_text(app)).group(0)
        assert code in status(app)
        await pilot.press("R")
        await submit_prompt(app, pilot, code)
        await wait_for(pilot, lambda: isinstance(app.screen, ConfirmScreen))
        assert "report.txt" in str(app.screen.query_one("#confirm-text").content)
        await pilot.press("y")
        await wait_for(pilot, lambda: (inbox / "report.txt").exists())
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert (inbox / "report.txt").read_text() == "quarterly"
        assert "received" in log_text(app) and f"{code}: received" in log_text(app)
        assert app.query_one("#history").row_count == 2


async def test_receive_declined(config_dir, ssh_server, client_key, tmp_path):
    src, inbox = write_full_config(ssh_server, client_key, tmp_path)
    f = tmp_path / "a.txt"
    f.write_text("a")
    app = EivlholeApp()
    app.poll = 0.1
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("s")
        await submit_prompt(app, pilot, str(f))
        await wait_for(pilot, lambda: "Code:" in log_text(app))
        code = CODE_RE.search(log_text(app)).group(0)
        await pilot.press("R")
        await submit_prompt(app, pilot, code)
        await wait_for(pilot, lambda: isinstance(app.screen, ConfirmScreen))
        await pilot.press("n")
        await wait_for(pilot, lambda: "cancelled" in status(app))
        assert not (inbox / "a.txt").exists()
        assert (ssh_server.root / "drop" / "codes" / code).exists()  # still claimable


async def test_send_missing_path_reports(config_dir, ssh_server, client_key, tmp_path):
    write_full_config(ssh_server, client_key, tmp_path)
    app = EivlholeApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("s")
        await submit_prompt(app, pilot, str(tmp_path / "ghost.txt"))
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert "no such file" in status(app)
        assert list((ssh_server.root / "drop" / "transfers").iterdir()) == []


async def test_receive_unknown_code_reports(config_dir, ssh_server, client_key, tmp_path):
    write_full_config(ssh_server, client_key, tmp_path)
    app = EivlholeApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("R")
        await submit_prompt(app, pilot, "9-nothing-here-now")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert "not found" in status(app)


async def test_navigation_during_worker(config_dir, ssh_server, client_key, tmp_path, monkeypatch):
    write_full_config(ssh_server, client_key, tmp_path)

    release = asyncio.Event()

    async def slow_run_job(name, config, **kwargs):
        await release.wait()
        return runner.RunResult(job=name, host="drop", status="ok", exit_code=0)

    monkeypatch.setattr(app_module, "run_job", slow_run_job)
    app = EivlholeApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("r")
        await pilot.pause()
        assert "running" in status(app)
        await pilot.press("2")
        await pilot.pause()
        assert isinstance(app.focused, HostsPanel)
        release.set()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert "ok" in status(app)


def test_split_paths_windows_and_posix(monkeypatch):
    monkeypatch.setattr(app_module.os, "name", "nt")
    got = app_module.split_paths(r'"C:\Program Files\x\a.txt" D:\b.txt')
    assert [str(p) for p in got] == [r"C:\Program Files\x\a.txt", r"D:\b.txt"]
    monkeypatch.setattr(app_module.os, "name", "posix")
    got = app_module.split_paths("'/home/me/a b.txt' /tmp/c")
    assert [str(p) for p in got] == ["/home/me/a b.txt", "/tmp/c"]


async def test_second_run_is_refused_while_running(
    config_dir, ssh_server, client_key, tmp_path, monkeypatch
):
    write_full_config(ssh_server, client_key, tmp_path)
    calls = []

    async def slow_run_job(name, config, **kwargs):
        calls.append(name)
        await asyncio.sleep(0.5)
        return runner.RunResult(job=name, host="drop", status="ok", exit_code=0)

    monkeypatch.setattr(app_module, "run_job", slow_run_job)
    app = EivlholeApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("r")
        await pilot.pause()
        await pilot.press("r")
        await pilot.pause()
        assert "already running" in status(app)
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert len(calls) == 1


async def test_failed_job_refreshes_history(
    config_dir, ssh_server, client_key, tmp_path, monkeypatch
):
    freeze_today(monkeypatch)
    src, _ = write_full_config(ssh_server, client_key, tmp_path)
    (src / "summary.xlsx").unlink()
    app = EivlholeApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("r")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app.query_one("#history").row_count == 1


async def wait_for_exit(app, timeout=15.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while app.return_code is None:
        if loop.time() > deadline:
            raise AssertionError("app did not exit in time")
        await asyncio.sleep(0.05)


async def test_quit_idle_does_not_prompt(config_dir, ssh_server, client_key, tmp_path):
    write_full_config(ssh_server, client_key, tmp_path)
    app = EivlholeApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("q")
        assert not isinstance(app.screen, ConfirmScreen)
        await wait_for_exit(app)
    assert app.return_code == 0


async def test_quit_during_job_asks_and_exits(
    config_dir, ssh_server, client_key, tmp_path, monkeypatch
):
    write_full_config(ssh_server, client_key, tmp_path)
    cancelled = []

    async def slow_run_job(name, config, **kwargs):
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            cancelled.append(name)
            raise
        return runner.RunResult(job=name, host="drop", status="ok", exit_code=0)

    monkeypatch.setattr(app_module, "run_job", slow_run_job)
    app = EivlholeApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("r")
        await pilot.pause()
        await pilot.press("q")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)
        assert "job" in str(app.screen.query_one("#confirm-text").content)
        await pilot.press("y")
        await wait_for_exit(app)
    assert app.return_code == 0
    assert cancelled == ["reports"]
    assert app.history is None


async def test_quit_declined_keeps_running(
    config_dir, ssh_server, client_key, tmp_path, monkeypatch
):
    write_full_config(ssh_server, client_key, tmp_path)

    async def slow_run_job(name, config, **kwargs):
        await asyncio.sleep(0.5)
        return runner.RunResult(job=name, host="drop", status="ok", exit_code=0)

    monkeypatch.setattr(app_module, "run_job", slow_run_job)
    app = EivlholeApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("r")
        await pilot.pause()
        await pilot.press("q")
        await pilot.pause()
        await pilot.press("n")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app.return_code is None
        assert "ok" in status(app)


async def test_quit_during_real_job_finishes_run_row(
    config_dir, ssh_server, client_key, tmp_path, monkeypatch
):
    from eivlhole.history import History

    freeze_today(monkeypatch)
    src, _ = write_full_config(ssh_server, client_key, tmp_path)
    (src / "summary.xlsx").write_bytes(b"x" * (5 * 1024 * 1024))
    real_upload = runner.upload_file
    started = asyncio.Event()

    async def slow_upload(*args, **kwargs):
        started.set()
        await asyncio.sleep(30)  # hold the real job mid-transfer until quit cancels it
        return await real_upload(*args, **kwargs)

    monkeypatch.setattr(runner, "upload_file", slow_upload)
    app = EivlholeApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("r")
        await asyncio.wait_for(started.wait(), 10)
        await pilot.press("q")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)
        await pilot.press("y")
        await wait_for_exit(app)
    with History(paths.history_file()) as h:
        runs = h.runs()
    assert len(runs) == 1 and runs[0].status not in ("running", "ok")


async def test_receive_confirm_shows_summary_for_many_files(
    config_dir, ssh_server, client_key, tmp_path
):
    write_full_config(ssh_server, client_key, tmp_path)
    many = tmp_path / "many"
    many.mkdir()
    for i in range(40):
        (many / f"f{i:02}.txt").write_text(str(i))
    app = EivlholeApp()
    app.poll = 0.1
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("s")
        await submit_prompt(app, pilot, str(many))
        await wait_for(pilot, lambda: "Code:" in log_text(app))
        code = CODE_RE.search(log_text(app)).group(0)
        await pilot.press("R")
        await submit_prompt(app, pilot, code)
        await wait_for(pilot, lambda: isinstance(app.screen, ConfirmScreen))
        body = str(app.screen.query_one("#confirm-text").content)
        assert body.startswith("40 file(s)")
        assert "and 25 more" in body
        await pilot.press("n")
        await wait_for(pilot, lambda: "cancelled" in status(app))


async def test_cancel_running_job(config_dir, ssh_server, client_key, tmp_path, monkeypatch):
    write_full_config(ssh_server, client_key, tmp_path)

    async def slow_run_job(name, config, **kwargs):
        await asyncio.sleep(5)
        return runner.RunResult(job=name, host="drop", status="ok", exit_code=0)

    monkeypatch.setattr(app_module, "run_job", slow_run_job)
    app = EivlholeApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("r")
        await pilot.pause()
        await pilot.press("c")
        await pilot.pause()
        assert "cancelled" in status(app)
        await wait_for(
            pilot, lambda: not any(w.group == "job" and w.is_running for w in app.workers)
        )
        await pilot.press("c")
        await pilot.pause()
        assert "nothing to cancel" in status(app)


async def test_retry_backoff_is_visible(config_dir, ssh_server, client_key, tmp_path):
    write_full_config(ssh_server, client_key, tmp_path)
    app = EivlholeApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await app._worker_sleep(0)
        await pilot.pause()
        assert "retrying in 0s" in log_text(app)
        assert "retrying in 0s" in status(app)


async def test_run_job_gets_visible_sleep(
    config_dir, ssh_server, client_key, tmp_path, monkeypatch
):
    write_full_config(ssh_server, client_key, tmp_path)
    seen = {}

    async def fake_run_job(name, config, **kwargs):
        seen["sleep"] = kwargs.get("sleep")
        await kwargs["sleep"](0)
        return runner.RunResult(job=name, host="drop", status="ok", exit_code=0)

    monkeypatch.setattr(app_module, "run_job", fake_run_job)
    app = EivlholeApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("r")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert seen["sleep"] == app._worker_sleep
        assert "retrying in 0s" in log_text(app)


async def test_edit_refused_while_running(
    config_dir, ssh_server, client_key, tmp_path, monkeypatch
):
    write_full_config(ssh_server, client_key, tmp_path)

    async def slow_run_job(name, config, **kwargs):
        await asyncio.sleep(0.5)
        return runner.RunResult(job=name, host="drop", status="ok", exit_code=0)

    def no_editor(*args, **kwargs):
        raise AssertionError("editor must not start")

    monkeypatch.setattr(app_module, "run_job", slow_run_job)
    monkeypatch.setattr(app_module.subprocess, "run", no_editor)
    monkeypatch.setattr(app_module, "editor_command", lambda: ["fake-editor"])
    app = EivlholeApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("r")
        await pilot.pause()
        await pilot.press("e")
        await pilot.pause()
        assert "finish or cancel" in status(app)
        await app.workers.wait_for_complete()


async def test_prompts_name_the_host(config_dir, ssh_server, client_key, tmp_path):
    from textual.widgets import Static

    from eivlhole.tui.screens import TextPrompt

    write_full_config(ssh_server, client_key, tmp_path)
    app = EivlholeApp()
    async with app.run_test(size=(120, 40)) as pilot:
        for key, word in (("s", "Send"), ("R", "Receive")):
            await pilot.press(key)
            await pilot.pause()
            assert isinstance(app.screen, TextPrompt)
            title = str(app.screen.query(Static).first().content)
            assert title.startswith(f"{word} via drop:")
            await pilot.press("escape")
            await pilot.pause()


def test_overwrite_hint_added(monkeypatch):
    app = EivlholeApp()
    seen = []
    monkeypatch.setattr(app, "set_status", lambda text, **kw: seen.append(text))
    monkeypatch.setattr(app, "write_log", lambda text: seen.append(text))
    app._report_failure("receive", RuntimeError("a.txt exists; pass --overwrite"))
    assert all("use the CLI with --overwrite" in t for t in seen) and len(seen) == 2
