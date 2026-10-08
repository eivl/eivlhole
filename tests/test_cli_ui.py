from typer.testing import CliRunner

from eivlhole.cli.app import app

runner = CliRunner()


def test_ui_command_is_registered():
    r = runner.invoke(app, ["--help"])
    assert r.exit_code == 0 and " ui " in r.output
    r = runner.invoke(app, ["ui", "--help"])
    assert r.exit_code == 0 and "terminal" in r.output.lower()


def test_ui_refuses_unattended(config_dir):
    r = runner.invoke(app, ["--unattended", "ui"])
    assert r.exit_code == 1 and "interactive" in r.output
