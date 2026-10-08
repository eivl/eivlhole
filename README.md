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

## Scheduled delivery (unattended)

A job names a host, a target directory and the files to send. Create the
target directory on the drop host once (`server init --incoming incoming/reports`),
then on the sending machine:

```
eivlhole jobs add weekly-reports --remote-dir /incoming/reports \
    --file "C:/reports/summary.xlsx" \
    --pattern "C:/reports/daily::^daily-\d{8}\.csv$" \
    --days tue,thu --time 07:00
eivlhole run weekly-reports --dry-run      # shows what would be sent
eivlhole run weekly-reports                # sends now; exit 0 ok, 1 failed, 2 skipped, 3 partial
eivlhole schedule install weekly-reports   # Task Scheduler on Windows, crontab on Linux
eivlhole schedule status weekly-reports
```

`run` never prompts: the host key must already be trusted (`hosts trust`) and
the key enrolled. Weekends, Norwegian public holidays and the job's `extra`
dates are skipped at run time; the OS scheduler only knows weekdays and the
time, so changing the calendar never needs the task re-registered.

On a weekend or holiday, `run --dry-run` reports `skipped: <reason>` and exits
2 instead of listing files; `run --force --dry-run` lists the files that would
be sent on such a day.

```
eivlhole holidays check 2026-04-03 --job weekly-reports
eivlhole holidays list --year 2026 --job weekly-reports
```

If `schedule install` cannot register the task (no rights to create scheduled
tasks, or `schtasks`/`crontab` missing), it prints the exact command and the
task XML or crontab line so an administrator can register it by hand. On Linux
it also refuses to write when `crontab -l` fails for any reason other than "no
crontab". `schedule print JOB` shows the same at any time. The Windows task
runs as S4U (no stored password): the tool's file-based key works, but keys
kept in a credential manager or on a redirected network profile do not. For
the same reason, job files and pattern directories must be on local disks: an
S4U task cannot reach mapped drives or UNC paths.

A remote file with the same name is replaced atomically on each run.
`on_success = "move"` moves sent files into a `sent/` folder next to them;
`"delete"` removes them; `"keep"` (default) leaves them. Recursive patterns
never look inside `sent/` folders.

## Transaction log

Every run and every file is recorded in `history.sqlite`:

```
eivlhole history files summary.xlsx "daily-*.csv" --on yesterday
eivlhole history files --job weekly-reports --since 2026-10-01 --status failed
eivlhole history runs --job weekly-reports -n 20
eivlhole history show 42
```

Add `--json` to any of them for one JSON object per line.

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

```toml
[jobs.weekly-reports]
host = "drop"
remote_dir = "/incoming/reports"
files = ["C:/reports/summary.xlsx"]
patterns = [
  { dir = "C:/reports/daily", regex = "^daily-\\d{8}\\.csv$", recursive = false },
]
schedule = { time = "07:00", days = ["tue", "thu"] }
holidays = { country = "NO", extra = ["2026-12-24", "2026-12-31", "2026-07-06..2026-07-24"] }
on_success = "keep"           # keep | move | delete
retries = 3
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
