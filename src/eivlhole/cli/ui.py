"""eivlhole ui: the terminal interface."""

import typer

from eivlhole.cli.common import get_state, handle_errors
from eivlhole.errors import EivlholeError


@handle_errors
def ui(ctx: typer.Context) -> None:
    """Open the terminal interface (jobs, hosts, history; send and receive)."""
    state = get_state(ctx)
    if state.unattended:
        raise EivlholeError("the terminal interface is interactive; drop --unattended")
    from eivlhole.tui.app import EivlholeApp  # imported here so `--help` stays fast

    EivlholeApp().run()
