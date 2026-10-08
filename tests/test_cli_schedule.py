import subprocess

from typer.testing import CliRunner

from eivlhole import paths
from eivlhole.cli.app import app
from eivlhole.schedule import base

runner = CliRunner()


def write_config(days='["tue", "thu"]'):
    paths.config_file().write_text(
        '[defaults]\nhost = "drop"\n\n[hosts.drop]\nhostname = "h"\nuser = "u"\n\n'
        '[jobs.reports]\nremote_dir = "/incoming"\n'
        f'schedule = {{ time = "07:00", days = {days} }}\n'
    )


class FakeRun:
    def __init__(self, returncode=0, stdout="", missing=False):
        self.returncode, self.stdout, self.missing = returncode, stdout, missing
        self.calls = []

    def __call__(self, args, **kwargs):
        self.calls.append(list(args))
        if self.missing:
            raise FileNotFoundError(args[0])
        return subprocess.CompletedProcess(args, self.returncode, stdout=self.stdout, stderr="")


def test_schedule_print_both_platforms(config_dir, monkeypatch):
    write_config()
    monkeypatch.setattr(base, "resolve_command", lambda: ["/usr/local/bin/eivlhole"])
    r = runner.invoke(app, ["schedule", "print", "reports", "--platform", "linux"])
    assert r.exit_code == 0 and "0 7 * * 2,4 /usr/local/bin/eivlhole" in r.output
    assert "# eivlhole:reports" in r.output and "crontab -e" in r.output
    r = runner.invoke(app, ["schedule", "print", "reports", "--platform", "windows"])
    assert r.exit_code == 0 and "<Tuesday />" in r.output and "schtasks /Create" in r.output


def test_schedule_install_success(config_dir, monkeypatch):
    write_config()
    run = FakeRun(stdout="")
    monkeypatch.setattr(base, "default_run", run)
    monkeypatch.setattr(base, "resolve_command", lambda: ["/usr/local/bin/eivlhole"])
    r = runner.invoke(app, ["schedule", "install", "reports", "--platform", "linux"])
    assert r.exit_code == 0, r.output
    assert "installed" in r.output and run.calls[-1] == ["crontab", "-"]


def test_schedule_install_fallback_prints_manual(config_dir, monkeypatch):
    write_config()
    monkeypatch.setattr(base, "default_run", FakeRun(missing=True))
    monkeypatch.setattr(base, "resolve_command", lambda: ["/usr/local/bin/eivlhole"])
    r = runner.invoke(app, ["schedule", "install", "reports", "--platform", "windows"])
    assert r.exit_code == 1
    assert "could not register" in r.output and "schtasks /Create" in r.output
    assert (config_dir / "schedule" / "reports.xml").exists()


def test_schedule_requires_days(config_dir):
    write_config(days="[]")
    r = runner.invoke(app, ["schedule", "print", "reports", "--platform", "linux"])
    assert r.exit_code == 1 and "schedule.days" in r.output


def test_schedule_status_and_uninstall(config_dir, monkeypatch):
    write_config()
    run = FakeRun(stdout="0 7 * * 2,4 /x --unattended run reports  # eivlhole:reports\n")
    monkeypatch.setattr(base, "default_run", run)
    r = runner.invoke(app, ["schedule", "status", "reports", "--platform", "linux"])
    assert r.exit_code == 0 and "eivlhole:reports" in r.output
    r = runner.invoke(app, ["schedule", "uninstall", "reports", "--platform", "linux"])
    assert r.exit_code == 0 and "removed" in r.output
    run.stdout = ""
    run.returncode = 1
    r = runner.invoke(app, ["schedule", "status", "reports", "--platform", "linux"])
    assert r.exit_code == 0 and "not installed" in r.output


def test_schedule_unknown_job(config_dir):
    write_config()
    r = runner.invoke(app, ["schedule", "print", "ghost", "--platform", "linux"])
    assert r.exit_code == 1 and "unknown job 'ghost'" in r.output
