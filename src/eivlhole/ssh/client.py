"""asyncssh connection helpers with our host-key policy and error mapping."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import asyncssh

from eivlhole import paths
from eivlhole.config import HostConfig
from eivlhole.errors import AuthError, ConnectError, HostKeyError

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SSHTarget:
    hostname: str
    port: int
    user: str
    key_path: Path | None
    use_agent: bool = False

    @property
    def label(self) -> str:
        return f"{self.user}@{self.hostname}:{self.port}"


TrustPrompt = Callable[[SSHTarget, str], bool]


def target_from_host(host: HostConfig) -> SSHTarget:
    key = Path(host.key).expanduser() if host.key else paths.private_key_file()
    return SSHTarget(
        hostname=host.hostname, port=host.port, user=host.user, key_path=key,
        use_agent=host.use_agent,
    )


def fingerprint(key: asyncssh.SSHKey) -> str:
    return key.get_fingerprint()


def _host_field(target: SSHTarget) -> str:
    return target.hostname if target.port == 22 else f"[{target.hostname}]:{target.port}"


def known_hosts_line(target: SSHTarget, key: asyncssh.SSHKey) -> str:
    return f"{_host_field(target)} {key.export_public_key().decode().strip()}"


def _read_lines(known_hosts: Path) -> list[str]:
    if not known_hosts.exists():
        return []
    return known_hosts.read_text(encoding="utf-8").splitlines()


def find_pinned(target: SSHTarget, known_hosts: Path) -> str | None:
    field = _host_field(target)
    for line in _read_lines(known_hosts):
        parts = line.split(maxsplit=1)
        if len(parts) == 2 and parts[0] == field:
            return parts[1].strip()
    return None


def pin_host_key(
    target: SSHTarget, key: asyncssh.SSHKey, known_hosts: Path, *, replace: bool = False
) -> bool:
    wanted = key.export_public_key().decode().strip()
    existing = find_pinned(target, known_hosts)
    if existing == wanted:
        return False
    if existing is not None and not replace:
        raise HostKeyError(
            f"host key for {target.label} has changed. If this is expected, run "
            f"`eivlhole hosts trust --replace`; otherwise someone may be intercepting."
        )
    field = _host_field(target)
    lines = [ln for ln in _read_lines(known_hosts) if ln.split(maxsplit=1)[:1] != [field]]
    lines.append(known_hosts_line(target, key))
    known_hosts.parent.mkdir(parents=True, exist_ok=True)
    known_hosts.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


async def fetch_host_key(target: SSHTarget) -> asyncssh.SSHKey:
    try:
        key = await asyncssh.get_server_host_key(target.hostname, target.port)
    except (OSError, asyncssh.Error) as exc:
        raise ConnectError(f"cannot reach {target.label}: {exc}") from exc
    if key is None:
        raise ConnectError(f"{target.label} did not present a host key")
    return key


def _auth_kwargs(target: SSHTarget, password: str | None) -> dict:
    kwargs: dict = {"agent_path": () if target.use_agent else None}
    if password is not None:
        kwargs["password"] = password
        kwargs["client_keys"] = []
        return kwargs
    if target.key_path is not None and target.key_path.exists():
        kwargs["client_keys"] = [str(target.key_path)]
    elif not target.use_agent:
        raise AuthError(
            f"no private key at {target.key_path}; run `eivlhole keys init` first"
        )
    else:
        kwargs["client_keys"] = []
    return kwargs


@asynccontextmanager
async def connect(
    target: SSHTarget,
    *,
    password: str | None = None,
    known_hosts: Path | None = None,
    prompt_trust: TrustPrompt | None = None,
) -> AsyncIterator[asyncssh.SSHClientConnection]:
    kh = known_hosts or paths.known_hosts_file()
    kh.parent.mkdir(parents=True, exist_ok=True)
    kh.touch(exist_ok=True)
    auth = _auth_kwargs(target, password)
    conn: asyncssh.SSHClientConnection | None = None
    for attempt in (1, 2):
        try:
            conn = await asyncssh.connect(
                target.hostname, target.port, username=target.user, known_hosts=str(kh), **auth
            )
            break
        except asyncssh.HostKeyNotVerifiable as exc:
            if attempt == 2 or prompt_trust is None:
                raise HostKeyError(
                    f"host key for {target.label} is not trusted; run "
                    f"`eivlhole hosts trust <name>` first"
                ) from exc
            if find_pinned(target, kh) is not None:
                raise HostKeyError(
                    f"host key for {target.label} has changed. If this is expected, run "
                    f"`eivlhole hosts trust --replace`; otherwise someone may be intercepting."
                ) from exc
            key = await fetch_host_key(target)
            if not prompt_trust(target, fingerprint(key)):
                raise HostKeyError(f"host key for {target.label} rejected by user") from exc
            pin_host_key(target, key, kh)
            log.info("pinned host key for %s", target.label)
        except asyncssh.PermissionDenied as exc:
            raise AuthError(
                f"authentication failed for {target.label}: {exc}. "
                f"Try `eivlhole keys test` or `eivlhole keys enrol`."
            ) from exc
        except (OSError, asyncssh.Error) as exc:
            raise ConnectError(f"cannot connect to {target.label}: {exc}") from exc
    assert conn is not None
    try:
        yield conn
    finally:
        conn.close()
        await conn.wait_closed()


@asynccontextmanager
async def sftp(
    target: SSHTarget,
    *,
    password: str | None = None,
    known_hosts: Path | None = None,
    prompt_trust: TrustPrompt | None = None,
) -> AsyncIterator[asyncssh.SFTPClient]:
    async with connect(
        target, password=password, known_hosts=known_hosts, prompt_trust=prompt_trust
    ) as conn:
        async with conn.start_sftp_client() as client:
            yield client
