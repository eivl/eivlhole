# eivlhole

Wormhole-style file transfer over SSH for networks without internet access,
plus scheduled unattended delivery (Plan 2) and a terminal UI (Plan 3).

Nothing runs on the server except OpenSSH. A central Linux "drop host" holds
transfers; senders get a short code, receivers type it.

## Install

Requires Python 3.13. With an Artifactory PyPI mirror:

```
uv tool install --index-url https://ARTIFACTORY/api/pypi/pypi/simple eivlhole
```

From a wheel carried over by USB (dependencies still resolve from Artifactory):

```
uv tool install --index-url https://ARTIFACTORY/api/pypi/pypi/simple ./eivlhole-0.1.0-py3-none-any.whl
```

Equivalent with pip: `pip install --index-url ... eivlhole`.

To make the index permanent, put this in `~/.config/uv/uv.toml` (Linux) or
`%APPDATA%\uv\uv.toml` (Windows):

```toml
[[index]]
name = "artifactory"
url = "https://ARTIFACTORY/api/pypi/pypi/simple"
default = true
```

## Drop host setup (Linux, once)

`server init` and the cron sweep run as root, so install eivlhole for root
with its command in a directory on root's PATH:

```
sudo UV_TOOL_BIN_DIR=/usr/local/bin uv tool install --index-url https://ARTIFACTORY/api/pypi/pypi/simple eivlhole
```

(or `sudo pip install --index-url ... eivlhole`). Then:

```
sudo useradd --system --home-dir /srv/eivlhole/home --shell "$(command -v nologin)" eivlhole
sudo passwd eivlhole                       # temporary, for key enrolment
sudo /usr/local/bin/eivlhole server init --root /srv/eivlhole --user eivlhole
```

`server init` prints an sshd `Match User` block. Add it to
`/etc/ssh/sshd_config` and reload sshd. The block uses
`ForceCommand internal-sftp -d /home` so that, inside the chroot, the SFTP
session starts in the service account's home and `eivlhole keys enrol` writes
the `authorized_keys` file sshd reads.

On PAM-based systems that use `pam_shells` (Arch Linux and derivatives), the
password login that `keys enrol` needs is refused unless the account's shell
is listed in `/etc/shells`. Either add it once:

    grep -qx "$(command -v nologin)" /etc/shells || command -v nologin | sudo tee -a /etc/shells

or skip password login entirely: run `eivlhole keys show` on each client and
append the line to `/srv/eivlhole/home/.ssh/authorized_keys` (directory 0700,
file 0600, both owned by `eivlhole`).

Scheduled jobs need a delivery directory the service account can write to;
create it with `--incoming` (repeatable) when you run `server init`, for
example `--incoming incoming/reports`, and use `remote_dir = "/incoming/reports"`
in the job.

Password login is only needed while clients run `eivlhole keys enrol`. Once
every client has enrolled its key:

1. lock the password: `sudo passwd -l eivlhole`
2. remove the `PasswordAuthentication yes` line from the `Match User` block
   and reload sshd.

Add a root cron entry for expiry, with the absolute path (cron's PATH does not
include `/usr/local/bin` on every distribution):

```
0 3 * * * /usr/local/bin/eivlhole server sweep --root /srv/eivlhole --max-age 7d
```

`codes/` and `transfers/` are mode 0733 and owned by root: the service
account can create and open entries by exact name but cannot list them.

## Client setup (each machine)

```
eivlhole hosts add drop --hostname drop.corp.local --user eivlhole
eivlhole hosts trust drop            # shows the fingerprint, pins it
eivlhole keys init                   # tool-managed ed25519 key
eivlhole keys enrol drop             # one-time password login, adds the key
eivlhole keys test drop
```

If password login is not allowed, `eivlhole keys show` prints the public key
line for an admin to add to the service account's `authorized_keys`.

With the chroot from `server init`, set `drop_root = "/"` under `[defaults]`
in the config file (`eivlhole config path` shows where it is).

## Usage

```
eivlhole send report.xlsx            # prints a code, waits for the receiver
eivlhole send --no-wait some/dir     # directories are sent as a tree
eivlhole receive 7-tunnel-stairway-glucose --out ~/inbox
```

Codes die when claimed. Transfers nobody claims are removed by `server sweep`.
If a receive fails, the waiting sender is told and the transfer stays on the
drop host until swept.

## Configuration

`config.toml` lives in `~/.config/eivlhole/` (Linux) or `%APPDATA%\eivlhole\`
(Windows); `EIVLHOLE_CONFIG_DIR` or `--config-dir` overrides it.

```toml
[defaults]
host = "drop"
drop_root = "/"
code_words = 3
receive_dir = "."

[hosts.drop]
hostname = "drop.corp.local"
port = 22
user = "eivlhole"
```

Every file movement is recorded in `history.sqlite` next to the config.

## Security model

Transport is SSH with per-machine ed25519 keys and strict host key pinning for
unattended runs. The drop host operator can read everything on it. Everyone
with the service account can read a transfer if they know its 128-bit id;
codes default to three words plus a number and are consumed on first claim.

## Development

```
uv sync
uv run pytest
uv run ruff check src tests
uv build
```
