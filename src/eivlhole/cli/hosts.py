"""eivlhole hosts: add, list, trust, remove."""

import typer
from rich.table import Table

from eivlhole.cli.common import console, get_state, handle_errors, run_async
from eivlhole.config import HostConfig
from eivlhole.errors import EivlholeError
from eivlhole.ssh.client import (
    fetch_host_key,
    find_pinned,
    fingerprint,
    pin_host_key,
    target_from_host,
)

hosts_app = typer.Typer(help="Manage SSH hosts.", no_args_is_help=True)


@hosts_app.command("add")
@handle_errors
def add(
    ctx: typer.Context,
    name: str,
    hostname: str = typer.Option(..., "--hostname", help="DNS name or IP."),
    user: str = typer.Option(..., "--user", help="SSH user, usually the service account."),
    port: int = typer.Option(22, "--port"),
    key: str = typer.Option("", "--key", help="Private key path; empty = tool-managed key."),
    use_agent: bool = typer.Option(False, "--use-agent", help="Also try ssh-agent/Pageant."),
    default: bool = typer.Option(False, "--default", help="Make this the default host."),
) -> None:
    """Add a host entry to config.toml."""
    state = get_state(ctx)
    cfg = state.config
    if name in cfg.hosts:
        raise EivlholeError(f"host '{name}' already exists; remove it first")
    cfg.hosts[name] = HostConfig(
        name=name, hostname=hostname, user=user, port=port, key=key, use_agent=use_agent
    )
    if default or cfg.defaults.host is None:
        cfg.defaults.host = name
    state.save()
    console.print(f"added host [bold]{name}[/] ({user}@{hostname}:{port})")
    if cfg.defaults.host == name:
        console.print(f"[dim]{name} is the default host[/]")
    console.print(f"Next: eivlhole hosts trust {name}")


@hosts_app.command("list")
@handle_errors
def list_(ctx: typer.Context) -> None:
    """List configured hosts."""
    state = get_state(ctx)
    cfg = state.config
    table = Table("name", "hostname", "port", "user", "key", "trusted", "default")
    for host in cfg.hosts.values():
        target = target_from_host(host)
        trusted = "yes" if find_pinned(target, state.known_hosts()) else "no"
        table.add_row(
            host.name, host.hostname, str(host.port), host.user, host.key or "(tool key)",
            trusted, "*" if cfg.defaults.host == host.name else "",
        )
    console.print(table)


@hosts_app.command("trust")
@handle_errors
def trust(
    ctx: typer.Context,
    name: str | None = typer.Argument(None, help="Host name; default host if omitted."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Do not ask for confirmation."),
    replace: bool = typer.Option(False, "--replace", help="Replace a changed host key."),
) -> None:
    """Fetch and pin the host key after showing its fingerprint."""
    state = get_state(ctx)
    host = state.config.host_for(name)
    target = target_from_host(host)
    key = run_async(fetch_host_key(target))
    console.print(f"{target.label} host key fingerprint: [bold]{fingerprint(key)}[/]")
    if not yes and not state.confirm("Trust this host?", default=False):
        raise EivlholeError("host not trusted")
    if pin_host_key(target, key, state.known_hosts(), replace=replace):
        console.print(f"pinned host key for {host.name}")
    else:
        console.print(f"host key for {host.name} already pinned")


@hosts_app.command("remove")
@handle_errors
def remove(ctx: typer.Context, name: str) -> None:
    """Remove a host entry."""
    state = get_state(ctx)
    cfg = state.config
    if name not in cfg.hosts:
        raise EivlholeError(f"unknown host '{name}'")
    users = [j.name for j in cfg.jobs.values() if j.host == name]
    if users:
        raise EivlholeError(f"host '{name}' is used by jobs: {', '.join(users)}")
    del cfg.hosts[name]
    if cfg.defaults.host == name:
        cfg.defaults.host = None
    state.save()
    console.print(f"removed host {name}")
