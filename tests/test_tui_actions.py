import os
import shutil
import subprocess

import pytest
from textual.widgets import Input

from eivlhole import paths
from eivlhole.errors import EivlholeError
from eivlhole.tui import app as app_module
from eivlhole.tui.app import EivlholeApp
from eivlhole.tui.panels import HistoryPanel
from eivlhole.tui.screens import TextPrompt
from tests.test_tui_app import seed_history, status, write_config


async def submit_prompt(app, pilot, text: str) -> None:
    await pilot.pause()
    assert isinstance(app.screen, TextPrompt)
    app.screen.query_one(Input).value = text
    await pilot.press("enter")
    await pilot.pause()


@pytest.fixture
def tui_env(config_dir):
    write_config()
    return seed_history()


async def test_filter_switches_history_to_files(tui_env):
    app = EivlholeApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("slash")
        await submit_prompt(app, pilot, "summary.xlsx")
        panel = app.query_one(HistoryPanel)
        assert panel.mode == "files" and panel.row_count == 1
        assert str(panel.get_row_at(0)[2]) == "summary.xlsx"
        assert "summary.xlsx" in panel.border_title
        await pilot.press("3")
        await pilot.pause()
        assert "sha256" in str(app.query_one("#detail").content)
        await pilot.press("slash")
        await submit_prompt(app, pilot, "")
        assert panel.mode == "runs" and panel.row_count == 2


async def test_filter_cancel_keeps_state(tui_env):
    app = EivlholeApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("slash")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert app.query_one(HistoryPanel).mode == "runs"


async def test_filter_bad_date_keeps_rows(tui_env):
    app = EivlholeApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("slash")
        await submit_prompt(app, pilot, "x.csv 2026-13-40")
        panel = app.query_one(HistoryPanel)
        assert "YYYY-MM-DD" in status(app)
        assert panel.mode == "runs" and panel.row_count == 2


async def test_edit_config_runs_editor_and_reloads(tui_env, monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        paths.config_file().write_text(
            paths.config_file().read_text() + '\n[jobs.nightly]\nremote_dir = "/n"\n'
        )
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(app_module.subprocess, "run", fake_run)
    monkeypatch.setattr(app_module, "editor_command", lambda: ["fake-editor"])
    app = EivlholeApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("e")
        await pilot.pause()
        assert calls and calls[0][0] == "fake-editor" and calls[0][-1] == str(paths.config_file())
        assert app.query_one("#jobs").row_count == 2
        assert "config reloaded" in status(app)


async def test_edit_without_editor_reports(tui_env, monkeypatch):
    def no_editor():
        raise EivlholeError("no editor found; set $EDITOR")

    monkeypatch.setattr(app_module, "editor_command", no_editor)
    app = EivlholeApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("e")
        await pilot.pause()
        assert "no editor found" in status(app)


async def test_test_host_ok(config_dir, ssh_server, client_key):
    write_config(host=ssh_server.host, port=ssh_server.port, key=client_key.as_posix())
    shutil.copy(ssh_server.known_hosts, paths.known_hosts_file())
    app = EivlholeApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("2")
        await pilot.press("t")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert "OK" in status(app) and "drop" in status(app)


async def test_test_host_untrusted_reports(config_dir, ssh_server, client_key):
    write_config(host=ssh_server.host, port=ssh_server.port, key=client_key.as_posix())
    app = EivlholeApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("t")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert "hosts trust" in status(app)


async def test_test_host_without_hosts(config_dir):
    app = EivlholeApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("t")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert "no host" in status(app)


async def test_edit_leaves_invalid_config_reports(tui_env, monkeypatch):
    def fake_run(cmd, **kwargs):
        paths.config_file().write_text("this is not toml")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(app_module.subprocess, "run", fake_run)
    monkeypatch.setattr(app_module, "editor_command", lambda: ["fake-editor"])
    app = EivlholeApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("e")
        await pilot.pause()
        assert "config has problems" in status(app)
        assert "config reloaded" not in status(app)


@pytest.mark.skipif(os.name != "posix" or os.geteuid() == 0, reason="needs POSIX non-root")
async def test_edit_config_unwritable_reports(config_dir, monkeypatch):
    monkeypatch.setattr(app_module, "editor_command", lambda: ["fake-editor"])
    app = EivlholeApp()
    async with app.run_test(size=(120, 40)) as pilot:
        config_dir.chmod(0o500)
        try:
            assert not paths.config_file().exists()
            await pilot.press("e")
            await pilot.pause()
            assert "cannot edit config" in status(app)
        finally:
            config_dir.chmod(0o700)
