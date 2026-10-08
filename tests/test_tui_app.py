import pytest

from eivlhole import paths
from eivlhole.history import History
from eivlhole.tui.app import EivlholeApp
from eivlhole.tui.panels import HistoryPanel, HostsPanel, JobsPanel
from eivlhole.tui.screens import HelpScreen


def write_config(host="10.0.0.5", port=22, key=""):
    paths.config_file().write_text(
        "[defaults]\n"
        'host = "drop"\n'
        'drop_root = "/drop"\n\n'
        "[hosts.drop]\n"
        f'hostname = "{host}"\n'
        f"port = {port}\n"
        'user = "eivlhole"\n' + (f'key = "{key}"\n' if key else "") + "\n[jobs.reports]\n"
        'remote_dir = "/incoming"\n'
        'files = ["/tmp/nope.xlsx"]\n'
        'schedule = { time = "07:00", days = ["tue", "thu"] }\n'
    )


def seed_history() -> tuple[int, int]:
    h = History(paths.history_file())
    r1 = h.start_run("job", job="reports", host="drop")
    h.add_file(
        r1,
        direction="out",
        filename="daily-20261007.csv",
        local_path="/l/d",
        remote_path="/r/d",
        size=10,
        sha256="aa",
        status="sent",
    )
    h.add_file(
        r1,
        direction="out",
        filename="summary.xlsx",
        local_path="/l/s",
        remote_path="/r/s",
        size=20,
        sha256="bb",
        status="sent",
    )
    h.finish_run(r1, "ok")
    r2 = h.start_run("receive", host="drop")
    h.finish_run(r2, "failed", exit_code=1, error="checksum mismatch")
    h.close()
    return r1, r2


@pytest.fixture
def tui_env(config_dir):
    write_config()
    return seed_history()


def status(app: EivlholeApp) -> str:
    return str(app.query_one("#status").content)


def detail(app: EivlholeApp) -> str:
    return str(app.query_one("#detail").content)


async def test_panels_populate_from_config_and_history(tui_env):
    r1, r2 = tui_env
    app = EivlholeApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        jobs = app.query_one(JobsPanel)
        hosts = app.query_one(HostsPanel)
        history = app.query_one(HistoryPanel)
        assert jobs.row_count == 1 and jobs.get_row_at(0)[0] == "reports"
        assert hosts.row_count == 1 and hosts.get_row_at(0)[0] == "drop"
        assert history.row_count == 2 and history.get_row_at(0)[0] == str(r2)  # newest first
        assert app.focused is jobs
        assert "reports" in detail(app) and "remote_dir" in detail(app)


async def test_panel_focus_and_detail(tui_env):
    r1, r2 = tui_env
    app = EivlholeApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("2")
        await pilot.pause()
        assert isinstance(app.focused, HostsPanel) and "10.0.0.5" in detail(app)
        await pilot.press("3")
        await pilot.pause()
        assert isinstance(app.focused, HistoryPanel) and f"run {r2}" in detail(app)
        await pilot.press("j")
        await pilot.pause()
        assert f"run {r1}" in detail(app) and "summary.xlsx" in detail(app)
        await pilot.press("k")
        await pilot.pause()
        assert f"run {r2}" in detail(app)
        await pilot.press("enter")
        await pilot.pause()
        assert "checksum mismatch" in detail(app)
        await pilot.press("1")
        await pilot.press("tab")
        await pilot.pause()
        assert isinstance(app.focused, HostsPanel)


async def test_help_screen(tui_env):
    app = EivlholeApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("question_mark")
        await pilot.pause()
        assert isinstance(app.screen, HelpScreen)
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, HelpScreen)


async def test_quit(tui_env):
    app = EivlholeApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("q")
        await pilot.pause()
    assert app.return_code == 0


async def test_empty_config_starts_cleanly(config_dir):
    app = EivlholeApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        assert app.query_one(JobsPanel).row_count == 0
        assert "no hosts or jobs configured" in status(app)
        await pilot.press("enter")
        await pilot.press("3")
        await pilot.press("enter")
        await pilot.pause()


async def test_invalid_config_reports(config_dir):
    paths.config_file().write_text('[hosts.bad]\nuser = "u"\n')
    app = EivlholeApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        assert "hosts.bad.hostname: required" in status(app)


async def test_history_unavailable_reports(config_dir):
    write_config()
    paths.history_file().mkdir()
    app = EivlholeApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        assert "history unavailable" in status(app)
        assert app.query_one(JobsPanel).row_count == 1


async def test_toml_syntax_error_status_is_kept(config_dir):
    paths.config_file().write_text("this is not toml")
    app = EivlholeApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        assert status(app).strip()
        assert "no hosts or jobs" not in status(app)


async def test_detail_failure_does_not_crash(tui_env, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("eivlhole.tui.app.data.job_detail", boom)
    app = EivlholeApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        assert "cannot show details" in detail(app)
        await pilot.press("2")
        await pilot.pause()
        assert isinstance(app.focused, HostsPanel)


async def test_corrupt_history_reports(config_dir):
    write_config()
    paths.history_file().write_bytes(b"not a database")
    app = EivlholeApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        assert "history unavailable" in status(app)
        assert app.query_one(JobsPanel).row_count == 1
