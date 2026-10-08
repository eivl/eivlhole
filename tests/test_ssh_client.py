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
        assert await s.getcwd() == "/home"


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


async def test_user_ssh_config_is_ignored(target, ssh_server, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    ssh_dir = tmp_path / ".ssh"
    ssh_dir.mkdir(exist_ok=True)
    config_text = "Host 127.0.0.1\n    HostName 127.0.0.2\n    Port 1\n    User nobody\n"
    (ssh_dir / "config").write_text(config_text, encoding="utf-8")
    async with client.connect(target, known_hosts=ssh_server.known_hosts) as conn:
        assert conn.get_extra_info("peername")[1] == ssh_server.port
    key = await client.fetch_host_key(target)
    assert client.fingerprint(key) == ssh_server.host_key.get_fingerprint()


def test_auth_kwargs_password_disables_keys(tmp_path):
    key = tmp_path / "id"
    key.write_text("x")
    kw = client._auth_kwargs(SSHTarget("h", 22, "u", key), "pw")
    assert kw["password"] == "pw"
    assert kw["client_keys"] is None
    assert kw["agent_path"] is None
    assert "agent_identities" not in kw


def test_auth_kwargs_key_only_restricts_agent(tmp_path):
    key = tmp_path / "id"
    key.write_text("x")
    (tmp_path / "id.pub").write_text("ssh-ed25519 AAAA")
    kw = client._auth_kwargs(SSHTarget("h", 22, "u", key), None)
    assert kw["client_keys"] == [str(key)]
    assert kw["agent_path"] is None
    assert kw["agent_identities"] == [str(key) + ".pub"]


def test_auth_kwargs_key_with_agent(tmp_path):
    key = tmp_path / "id"
    key.write_text("x")
    kw = client._auth_kwargs(SSHTarget("h", 22, "u", key, use_agent=True), None)
    assert kw == {"client_keys": [str(key)], "agent_path": ()}


def test_auth_kwargs_agent_without_key(tmp_path):
    kw = client._auth_kwargs(SSHTarget("h", 22, "u", tmp_path / "missing", use_agent=True), None)
    assert kw == {"client_keys": [], "agent_path": ()}


def test_auth_kwargs_no_key_no_agent(tmp_path):
    with pytest.raises(AuthError, match="keys init"):
        client._auth_kwargs(SSHTarget("h", 22, "u", tmp_path / "missing"), None)


def _default_home_key(tmp_path, monkeypatch) -> str:
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("SSH_AUTH_SOCK", raising=False)
    key = asyncssh.generate_private_key("ssh-ed25519")
    key.write_private_key(str(home / ".ssh" / "id_ed25519"))
    key.write_public_key(str(home / ".ssh" / "id_ed25519.pub"))
    return key.export_public_key().decode().strip()


async def test_key_auth_ignores_default_home_keys(target, ssh_server, tmp_path, monkeypatch):
    default_key = _default_home_key(tmp_path, monkeypatch)
    async with client.connect(target, known_hosts=ssh_server.known_hosts):
        pass
    assert ssh_server.offered
    assert default_key not in ssh_server.offered


async def test_password_auth_offers_no_keys(ssh_server, tmp_path, monkeypatch):
    default_key = _default_home_key(tmp_path, monkeypatch)
    wrong = tmp_path / "wrong"
    asyncssh.generate_private_key("ssh-ed25519").write_private_key(str(wrong))
    t = SSHTarget(ssh_server.host, ssh_server.port, ssh_server.user, wrong)
    async with client.connect(t, password="pw", known_hosts=ssh_server.known_hosts):
        pass
    assert default_key not in ssh_server.offered
    assert ssh_server.offered == []
