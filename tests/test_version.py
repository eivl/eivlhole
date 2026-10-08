from typer.testing import CliRunner

from eivlhole import __version__
from eivlhole.cli.app import app

runner = CliRunner()


def test_version_flag_prints_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output
    assert "eivlhole" in result.output
