"""Shared fixtures: an in-process SSH/SFTP server with password and key auth."""

from __future__ import annotations

import asyncio
import contextlib
import gc
import os
import posixpath
import threading
from dataclasses import dataclass, field
from pathlib import Path

import asyncssh
import pytest

from eivlhole import paths
from eivlhole.ssh.client import SSHTarget


@dataclass
class FakeServer:
    host: str
    port: int
    root: Path
    user: str
    password: str
    host_key: asyncssh.SSHKey
    known_hosts: Path
    authorized: set[str] = field(default_factory=set)
    sftp_cwd: bytes = b"/home"
    offered: list[str] = field(default_factory=list)

    def authorized_keys_file(self) -> Path:
        return self.root / "home" / ".ssh" / "authorized_keys"

    def accepts(self, key: asyncssh.SSHKey) -> bool:
        wanted = key.export_public_key().decode().split()[:2]
        lines = set(self.authorized)
        f = self.authorized_keys_file()
        if f.exists():
            lines |= set(f.read_text(encoding="utf-8").splitlines())
        return any(line.split()[:2] == wanted for line in lines if line.strip())


@pytest.fixture
def ssh_server(tmp_path: Path):
    root = tmp_path / "srv"
    root.mkdir()
    (root / "home").mkdir()
    host_key = asyncssh.generate_private_key("ssh-ed25519")
    server = FakeServer(
        host="127.0.0.1",
        port=0,
        root=root,
        user="eivlhole",
        password="pw",
        host_key=host_key,
        known_hosts=tmp_path / "server_known_hosts",
    )

    conns: list[asyncssh.SSHServerConnection] = []

    class Server(asyncssh.SSHServer):
        def connection_made(self, conn: asyncssh.SSHServerConnection) -> None:
            conns.append(conn)

        def begin_auth(self, username: str) -> bool:
            return True

        def password_auth_supported(self) -> bool:
            return True

        def validate_password(self, username: str, password: str) -> bool:
            return username == server.user and password == server.password

        def public_key_auth_supported(self) -> bool:
            return True

        def validate_public_key(self, username: str, key: asyncssh.SSHKey) -> bool:
            server.offered.append(key.export_public_key().decode().strip())
            return username == server.user and server.accepts(key)

    class SFTP(asyncssh.SFTPServer):
        """Models sshd's `ChrootDirectory root` + `ForceCommand internal-sftp -d /home`.

        Absolute paths resolve inside the chroot; relative paths resolve against
        the working directory /home, as they do for sftp-server after its chdir.
        """

        def __init__(self, chan: asyncssh.SSHServerChannel):
            super().__init__(chan, chroot=str(root))

        def map_path(self, path: bytes) -> bytes:
            if not path.startswith(b"/"):
                path = posixpath.join(server.sftp_cwd, path)
            return super().map_path(path)

    loop = asyncio.new_event_loop()
    ready = threading.Event()
    holder: dict[str, asyncssh.SSHAcceptor] = {}

    async def serve() -> None:
        listener = await asyncssh.listen(
            server.host,
            0,
            server_host_keys=[host_key],
            server_factory=Server,
            sftp_factory=SFTP,
            allow_scp=False,
        )
        holder["listener"] = listener
        server.port = listener.sockets[0].getsockname()[1]
        ready.set()

    def run() -> None:
        asyncio.set_event_loop(loop)
        loop.run_until_complete(serve())
        loop.run_forever()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    assert ready.wait(10), "test SSH server did not start"
    server.known_hosts.write_text(
        f"[{server.host}]:{server.port} {host_key.export_public_key().decode().strip()}\n",
        encoding="utf-8",
    )
    yield server

    async def shutdown() -> None:
        # Close accepted connections and the listener inside the loop so their
        # sockets are released before the loop stops (otherwise Windows reports
        # them as unraisable ResourceWarnings at garbage-collection time).
        listener = holder["listener"]
        listener.close()
        for conn in conns:
            conn.close()
        for conn in conns:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(conn.wait_closed(), 5)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(listener.wait_closed(), 5)
        pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        await loop.shutdown_asyncgens()

    with contextlib.suppress(Exception):
        asyncio.run_coroutine_threadsafe(shutdown(), loop).result(20)
    loop.call_soon_threadsafe(loop.stop)
    thread.join(10)
    loop.close()


@pytest.fixture(autouse=True)
def _collect_garbage():
    """Make leaked sockets and database handles warn inside the test that leaked them."""
    yield
    gc.collect()


@pytest.fixture
def client_key(tmp_path: Path, ssh_server: FakeServer) -> Path:
    keydir = tmp_path / "keys"
    keydir.mkdir()
    key = asyncssh.generate_private_key("ssh-ed25519", comment="test")
    priv = keydir / "id_ed25519"
    key.write_private_key(str(priv))
    key.write_public_key(str(keydir / "id_ed25519.pub"))
    if os.name == "posix":
        os.chmod(priv, 0o600)
    ssh_server.authorized.add(key.export_public_key().decode().strip())
    return priv


@pytest.fixture
def target(ssh_server: FakeServer, client_key: Path) -> SSHTarget:
    return SSHTarget(
        hostname=ssh_server.host,
        port=ssh_server.port,
        user=ssh_server.user,
        key_path=client_key,
    )


@pytest.fixture
def config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "cfg"
    monkeypatch.setenv(paths.ENV_CONFIG_DIR, str(d))
    paths.ensure_dirs()
    return d
