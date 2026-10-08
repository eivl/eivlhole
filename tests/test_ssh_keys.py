import os

import asyncssh
import pytest

from eivlhole.errors import AuthError, EivlholeError
from eivlhole.ssh import keys
from eivlhole.ssh.client import SSHTarget


def test_generate_keypair(tmp_path):
    priv, pub = keys.generate_keypair(tmp_path / "k" / "id_ed25519")
    assert priv.exists() and pub.exists()
    assert asyncssh.read_private_key(str(priv)).get_algorithm() == "ssh-ed25519"
    line = keys.public_key_line(pub)
    assert line.startswith("ssh-ed25519 ") and "eivlhole@" in line and "\n" not in line
    if os.name == "posix":
        assert priv.stat().st_mode & 0o777 == 0o600


def test_generate_refuses_overwrite(tmp_path):
    keys.generate_keypair(tmp_path / "id")
    with pytest.raises(EivlholeError, match="already exists"):
        keys.generate_keypair(tmp_path / "id")
    keys.generate_keypair(tmp_path / "id", force=True)


def test_public_key_line_missing(tmp_path):
    with pytest.raises(EivlholeError, match="keys init"):
        keys.public_key_line(tmp_path / "nope.pub")


async def test_enrol_then_key_auth_works(ssh_server, tmp_path):
    priv, pub = keys.generate_keypair(tmp_path / "id")
    line = keys.public_key_line(pub)
    t = SSHTarget(ssh_server.host, ssh_server.port, ssh_server.user, priv)
    with pytest.raises(AuthError):
        await keys.test_connection(t, known_hosts=ssh_server.known_hosts)
    assert await keys.enrol(t, "pw", line, known_hosts=ssh_server.known_hosts) is True
    assert ssh_server.authorized_keys_file().read_text().strip() == line
    assert await keys.enrol(t, "pw", line, known_hosts=ssh_server.known_hosts) is False
    assert await keys.test_connection(t, known_hosts=ssh_server.known_hosts) == "/"


async def test_enrol_appends_to_existing_file(ssh_server, tmp_path):
    f = ssh_server.authorized_keys_file()
    f.parent.mkdir()
    f.write_text("ssh-ed25519 AAAAexisting other@host")  # no trailing newline
    priv, pub = keys.generate_keypair(tmp_path / "id")
    t = SSHTarget(ssh_server.host, ssh_server.port, ssh_server.user, priv)
    await keys.enrol(t, "pw", keys.public_key_line(pub), known_hosts=ssh_server.known_hosts)
    lines = f.read_text().splitlines()
    assert lines[0] == "ssh-ed25519 AAAAexisting other@host"
    assert lines[1] == keys.public_key_line(pub)
    assert len(lines) == 2


async def test_enrol_bad_password(ssh_server, tmp_path):
    priv, pub = keys.generate_keypair(tmp_path / "id")
    t = SSHTarget(ssh_server.host, ssh_server.port, ssh_server.user, priv)
    with pytest.raises(AuthError):
        await keys.enrol(t, "wrong", keys.public_key_line(pub), known_hosts=ssh_server.known_hosts)


def test_public_key_line_empty_file(tmp_path):
    pub = tmp_path / "id.pub"
    pub.write_text("")
    with pytest.raises(EivlholeError, match="empty"):
        keys.public_key_line(pub)
