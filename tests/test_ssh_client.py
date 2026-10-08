from pathlib import Path

import asyncssh
import pytest

from eivlhole.config import HostConfig
from eivlhole.errors import AuthError, ConnectError, HostKeyError
from eivlhole.ssh import client
from eivlhole.ssh.client import SSHTarget


def test_target_from_host_uses_tool_key_by_default(config_dir):
    t = client.target_from_host(HostConfig(name="d", hostname="h", user="u", port=2222))
    expected_key = config_dir / "keys" / "id_ed25519"
    assert t == SSHTarget(hostname="h", port=2222, user="u", key_path=expected_key)
    assert t.label == "u@h:2222"


def test_target_from_host_custom_key(config_dir, tmp_path):
    t = client.target_from_host(HostConfig(name="d", hostname="h", user="u", key="~/k"))
    assert t.key_path == Path("~/k").expanduser()


def test_known_hosts_line_port_22():
    key = asyncssh.generate_private_key("ssh-ed25519")
    t = SSHTarget("h", 22, "u", None)
    assert client.known_hosts_line(t, key).startswith("h ssh-ed25519 ")
    t2 = SSHTarget("h", 2222, "u", None)
    assert client.known_hosts_line(t2, key).startswith("[h]:2222 ssh-ed25519 ")


def test_pin_host_key(tmp_path):
    kh = tmp_path / "kh"
    key = asyncssh.generate_private_key("ssh-ed25519")
    other = asyncssh.generate_private_key("ssh-ed25519")
    t = SSHTarget("h", 2222, "u", None)
    assert client.pin_host_key(t, key, kh) is True
    assert client.pin_host_key(t, key, kh) is False
    assert kh.read_text().count("\n") == 1
    with pytest.raises(HostKeyError, match="changed"):
        client.pin_host_key(t, other, kh)
    assert client.pin_host_key(t, other, kh, replace=True) is True
    assert client.find_pinned(t, kh) == other.export_public_key().decode().strip()
    assert client.find_pinned(SSHTarget("x", 1, "u", None), kh) is None


async def test_connect_with_key_and_pinned_host(target, ssh_server):
    async with client.connect(target, known_hosts=ssh_server.known_hosts) as conn:
        assert conn.get_extra_info("peername")[1] == ssh_server.port
    async with client.sftp(target, known_hosts=ssh_server.known_hosts) as s:
        assert await s.getcwd() == "/"


async def test_connect_with_password(ssh_server, tmp_path):
    t = SSHTarget(ssh_server.host, ssh_server.port, ssh_server.user, tmp_path / "missing")
    async with client.connect(t, password="pw", known_hosts=ssh_server.known_hosts) as conn:
        assert conn is not None


async def test_bad_password_is_auth_error(ssh_server, tmp_path):
    t = SSHTarget(ssh_server.host, ssh_server.port, ssh_server.user, tmp_path / "missing")
    with pytest.raises(AuthError):
        async with client.connect(t, password="nope", known_hosts=ssh_server.known_hosts):
            pass


async def test_unknown_host_without_prompt_is_host_key_error(target, tmp_path):
    with pytest.raises(HostKeyError, match="hosts trust"):
        async with client.connect(target, known_hosts=tmp_path / "empty"):
            pass


async def test_unknown_host_prompt_accept_pins(target, tmp_path, ssh_server):
    kh = tmp_path / "empty"
    seen = []

    def prompt(t, fp):
        seen.append((t, fp))
        return True

    async with client.connect(target, known_hosts=kh, prompt_trust=prompt):
        pass
    assert seen[0][0] == target and seen[0][1].startswith("SHA256:")
    expected = ssh_server.host_key.export_public_key().decode().strip()
    assert client.find_pinned(target, kh) == expected
    async with client.connect(target, known_hosts=kh):  # now trusted, no prompt
        pass


async def test_unknown_host_prompt_decline(target, tmp_path):
    with pytest.raises(HostKeyError, match="rejected"):
        async with client.connect(
            target, known_hosts=tmp_path / "empty", prompt_trust=lambda t, fp: False
        ):
            pass


async def test_changed_host_key_is_error_even_with_prompt(target, tmp_path):
    kh = tmp_path / "kh"
    other = asyncssh.generate_private_key("ssh-ed25519")
    client.pin_host_key(target, other, kh)
    with pytest.raises(HostKeyError, match="changed"):
        async with client.connect(target, known_hosts=kh, prompt_trust=lambda t, fp: True):
            pass


async def test_connection_refused_is_connect_error(tmp_path, client_key):
    t = SSHTarget("127.0.0.1", 1, "u", client_key)
    with pytest.raises(ConnectError):
        async with client.connect(t, known_hosts=tmp_path / "kh"):
            pass


async def test_fetch_host_key(target, ssh_server):
    key = await client.fetch_host_key(target)
    assert client.fingerprint(key) == ssh_server.host_key.get_fingerprint()
