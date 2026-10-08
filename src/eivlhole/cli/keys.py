"""eivlhole keys: init, show, enrol, test."""

import sys

import typer

from eivlhole import paths
from eivlhole.cli.common import console, get_state, handle_errors, run_async
from eivlhole.errors import EivlholeError
from eivlhole.ssh import keys
from eivlhole.ssh.client import target_from_host

keys_app = typer.Typer(help="Manage the tool's SSH key.", no_args_is_help=True)


@keys_app.command("init")
@handle_errors
def init(
    ctx: typer.Context,
    force: bool = typer.Option(False, "--force", help="Replace an existing key."),
) -> None:
    """Generate the tool-managed ed25519 key."""
    get_state(ctx)
    priv, pub = keys.generate_keypair(paths.private_key_file(), force=force)
    console.print(f"generated {priv}", markup=False)
    console.print("Public key (give this to an admin, or run `eivlhole keys enrol`):")
    console.print(keys.public_key_line(pub), markup=False, highlight=False)


@keys_app.command("show")
@handle_errors
def show(ctx: typer.Context) -> None:
    """Print the public key line for authorized_keys."""
    get_state(ctx)
    console.print(keys.public_key_line(paths.public_key_file()), markup=False, highlight=False)


@keys_app.command("enrol")
@handle_errors
def enrol(
    ctx: typer.Context,
    name: str | None = typer.Argument(None, help="Host name; default host if omitted."),
    password_stdin: bool = typer.Option(
        False, "--password-stdin", help="Read the password from stdin instead of prompting."
    ),
) -> None:
    """Log in once with a password and add the public key to authorized_keys."""
    state = get_state(ctx)
    host = state.config.host_for(name)
    target = target_from_host(host)
    line = keys.public_key_line(paths.public_key_file())
    if password_stdin:
        password = sys.stdin.readline().rstrip("\r\n")
    elif state.unattended:
        raise EivlholeError("enrol needs a password; use --password-stdin in unattended mode")
    else:
        password = typer.prompt(f"Password for {target.label}", hide_input=True)
    added = run_async(keys.enrol(
        target, password, line, known_hosts=state.known_hosts(), prompt_trust=state.trust_prompt()
    ))
    if added:
        console.print(f"enrolled public key on {target.label}", markup=False)
    else:
        console.print(f"public key already present on {target.label}", markup=False)


@keys_app.command("test")
@handle_errors
def test(
    ctx: typer.Context,
    name: str | None = typer.Argument(None, help="Host name; default host if omitted."),
) -> None:
    """Verify key-based login and SFTP access."""
    state = get_state(ctx)
    host = state.config.host_for(name)
    target = target_from_host(host)
    cwd = run_async(keys.test_connection(
        target, known_hosts=state.known_hosts(), prompt_trust=state.trust_prompt()
    ))
    console.print(f"OK: {target.label} (sftp cwd {cwd})", markup=False)
