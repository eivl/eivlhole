"""Create the drop host directory layout. Run on the drop host, ideally as root."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

CODES = "codes"
TRANSFERS = "transfers"
HOME = "home"

_MODES = {"": 0o755, CODES: 0o733, TRANSFERS: 0o733, HOME: 0o700}


@dataclass
class InitResult:
    created: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def init_layout(root: Path, *, user: str = "eivlhole", dry_run: bool = False) -> InitResult:
    result = InitResult()
    is_root = os.name == "posix" and os.geteuid() == 0
    for name, mode in _MODES.items():
        path = root / name if name else root
        if not path.exists():
            result.created.append(path)
            if not dry_run:
                path.mkdir(parents=True)
        if dry_run:
            continue
        if os.name == "posix":
            os.chmod(path, mode)
            if is_root:
                if name == HOME:
                    try:
                        shutil.chown(path, user=user, group=user)
                    except (LookupError, PermissionError) as exc:
                        result.warnings.append(f"could not chown {path} to {user}: {exc}")
                else:
                    shutil.chown(path, user="root")
    if os.name == "posix" and not is_root:
        result.warnings.append(
            f"not running as root: {root / CODES} and {root / TRANSFERS} should be owned by "
            f"root so the service account cannot list them. Re-run with sudo."
        )
    if os.name != "posix":
        result.warnings.append(
            "Windows drop host: directories are listable by the service account; "
            "codes are claim-once but not hidden."
        )
    return result


def sshd_snippet(root: Path, user: str) -> str:
    return f"""# Create the service account (home inside the chroot so keys enrol works):
#   useradd --system --home-dir {root / HOME} --shell /usr/sbin/nologin {user}
#   passwd {user}        # temporary password for `eivlhole keys enrol`
#
# Add to /etc/ssh/sshd_config, then `systemctl reload sshd`:
Match User {user}
    ChrootDirectory {root}
    # -d /home: start in the home dir so `keys enrol` writes /home/.ssh/authorized_keys,
    # which sshd reads before the chroot as {root / HOME}/.ssh/authorized_keys.
    ForceCommand internal-sftp -d /home
    AllowTcpForwarding no
    X11Forwarding no
    # needed only while clients run `eivlhole keys enrol`; afterwards run
    # `passwd -l {user}` and remove this line
    PasswordAuthentication yes
#
# Clients then use:
#   [defaults]
#   drop_root = "/"
# and cron on this host:
#   0 3 * * * eivlhole server sweep --root {root} --max-age 7d
"""
