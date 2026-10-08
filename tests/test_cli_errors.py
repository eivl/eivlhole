import asyncssh
import pytest
import typer

from eivlhole import paths
from eivlhole.cli.common import handle_errors, setup_logging


@pytest.mark.parametrize(
    "exc, expected",
    [
        (asyncssh.SFTPPermissionDenied("x"), "remote file error: x"),
        (asyncssh.SFTPNoSuchFile("gone"), "remote file error: gone"),
        (asyncssh.PermissionDenied("nope"), "permission denied: nope"),
        (asyncssh.KeyImportError("bad key"), "cannot load private key: bad key"),
        (asyncssh.KeyEncryptionError("locked"), "cannot load private key: locked"),
        (asyncssh.ConnectionLost("lost"), "connection error: lost"),
        (ConnectionResetError("reset"), "connection error: reset"),
    ],
)
def test_handle_errors_maps_ssh_errors(config_dir, capsys, exc, expected):
    setup_logging(False)

    @handle_errors
    def boom() -> None:
        raise exc

    with pytest.raises(typer.Exit) as info:
        boom()
    assert info.value.exit_code == 1
    captured = capsys.readouterr()
    assert expected in captured.err
    assert "Traceback" not in captured.err
    assert expected in (paths.log_dir() / "eivlhole.log").read_text()
