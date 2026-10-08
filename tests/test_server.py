import os
import time
from datetime import UTC, datetime, timedelta

import pytest
from typer.testing import CliRunner

from eivlhole.cli.app import app
from eivlhole.errors import EivlholeError
from eivlhole.server import layout, sweep

runner = CliRunner()


def test_init_layout_creates_dirs(tmp_path):
    root = tmp_path / "srv"
    result = layout.init_layout(root, user="eivlhole")
    assert (root / "codes").is_dir() and (root / "transfers").is_dir() and (root / "home").is_dir()
    assert {p.name for p in result.created} >= {"codes", "transfers", "home"}
    if os.name == "posix":
        assert (root / "codes").stat().st_mode & 0o777 == 0o733
        assert (root / "transfers").stat().st_mode & 0o777 == 0o733
        assert (root / "home").stat().st_mode & 0o777 == 0o700
        if os.geteuid() != 0:
            assert any("root" in w for w in result.warnings)
    result2 = layout.init_layout(root)
    assert result2.created == []


def test_init_layout_dry_run(tmp_path):
    root = tmp_path / "srv"
    result = layout.init_layout(root, dry_run=True)
    assert not root.exists() and len(result.created) == 4


def test_sshd_snippet(tmp_path):
    text = layout.sshd_snippet(tmp_path / "srv", "eivlhole")
    assert "Match User eivlhole" in text
    assert f"ChrootDirectory {tmp_path / 'srv'}" in text
    assert "ForceCommand internal-sftp -d /home" in text
    assert "passwd -l eivlhole" in text
    assert 'drop_root = "/"' in text


@pytest.mark.parametrize(
    "text, expected",
    [
        ("7d", timedelta(days=7)),
        ("12h", timedelta(hours=12)),
        ("30m", timedelta(minutes=30)),
        ("90s", timedelta(seconds=90)),
        ("2D", timedelta(days=2)),
    ],
)
def test_parse_duration(text, expected):
    assert sweep.parse_duration(text) == expected


@pytest.mark.parametrize("bad", ["", "7", "d7", "7w", "-1d", "1.5d"])
def test_parse_duration_rejects(bad):
    with pytest.raises(EivlholeError):
        sweep.parse_duration(bad)


def make_transfer(root, tid, *, age: timedelta, claimed=False, manifest=True, code=None):
    d = root / "transfers" / tid
    d.mkdir(parents=True)
    (d / "payload").write_bytes(b"x")
    if manifest:
        (d / "manifest.json").write_text("{}")
    if claimed:
        (d / "claimed").write_text("")
    when = time.time() - age.total_seconds()
    for p in [d, *d.iterdir()]:
        os.utime(p, (when, when))
    if code:
        c = root / "codes" / code
        c.write_text(tid + "\n")
        os.utime(c, (when, when))
    return d


def test_sweep_removes_old_and_stale(tmp_path):
    root = tmp_path / "srv"
    layout.init_layout(root)
    old = "a" * 32
    fresh = "b" * 32
    make_transfer(root, old, age=timedelta(days=8), code="1-old-old")
    make_transfer(root, fresh, age=timedelta(hours=1), code="2-new-new")
    (root / "codes" / "3-dangling").write_text("c" * 32 + "\n")
    result = sweep.sweep(root, timedelta(days=7))
    assert result.removed_transfers == [old]
    assert sorted(result.removed_codes) == ["1-old-old", "3-dangling"]
    assert result.kept_transfers == 1
    assert not (root / "transfers" / old).exists()
    assert (root / "transfers" / fresh).exists() and (root / "codes" / "2-new-new").exists()


def test_sweep_dry_run_changes_nothing(tmp_path):
    root = tmp_path / "srv"
    layout.init_layout(root)
    make_transfer(root, "a" * 32, age=timedelta(days=30), code="1-x-y")
    result = sweep.sweep(root, timedelta(days=7), dry_run=True)
    assert result.removed_transfers == ["a" * 32] and result.removed_codes == ["1-x-y"]
    assert (root / "transfers" / ("a" * 32)).exists() and (root / "codes" / "1-x-y").exists()


def test_sweep_uses_newest_mtime(tmp_path):
    root = tmp_path / "srv"
    layout.init_layout(root)
    d = make_transfer(root, "a" * 32, age=timedelta(days=30))
    (d / "manifest.json").write_text("{}")  # fresh manifest = upload just finished
    result = sweep.sweep(root, timedelta(days=7))
    assert result.removed_transfers == []


def test_sweep_with_explicit_now(tmp_path):
    root = tmp_path / "srv"
    layout.init_layout(root)
    make_transfer(root, "a" * 32, age=timedelta(hours=1))
    future = datetime.now(UTC) + timedelta(days=10)
    assert sweep.sweep(root, timedelta(days=7), now=future).removed_transfers == ["a" * 32]


def test_sweep_missing_root(tmp_path):
    with pytest.raises(EivlholeError, match="server init"):
        sweep.sweep(tmp_path / "nope", timedelta(days=7))


def test_status(tmp_path):
    root = tmp_path / "srv"
    layout.init_layout(root)
    assert sweep.status(root) == {"transfers": 0, "claimed": 0, "codes": 0, "oldest_hours": None}
    make_transfer(root, "a" * 32, age=timedelta(hours=5), claimed=True, code="1-a-b")
    make_transfer(root, "b" * 32, age=timedelta(hours=2))
    st = sweep.status(root)
    assert st["transfers"] == 2 and st["claimed"] == 1 and st["codes"] == 1
    assert 4.9 < st["oldest_hours"] < 5.1


def test_cli_server_init_sweep_status(tmp_path, config_dir):
    root = tmp_path / "srv"
    r = runner.invoke(app, ["server", "init", "--root", str(root), "--user", "svc"])
    assert r.exit_code == 0, r.output
    assert "Match User svc" in r.output and (root / "codes").is_dir()
    make_transfer(root, "a" * 32, age=timedelta(days=9), code="1-a-b")
    r = runner.invoke(app, ["server", "status", "--root", str(root)])
    assert r.exit_code == 0 and "transfers: 1" in r.output
    r = runner.invoke(app, ["server", "sweep", "--root", str(root), "--max-age", "7d", "--dry-run"])
    assert r.exit_code == 0 and "would remove 1 transfer" in r.output
    r = runner.invoke(app, ["server", "sweep", "--root", str(root), "--max-age", "7d"])
    assert r.exit_code == 0 and "removed 1 transfer" in r.output
    r = runner.invoke(app, ["server", "sweep", "--root", str(root), "--max-age", "7x"])
    assert r.exit_code == 1


def test_sweep_keeps_fresh_pair_and_removes_non_hex_code(tmp_path):
    root = tmp_path / "srv"
    layout.init_layout(root)
    make_transfer(root, "a" * 32, age=timedelta(hours=1), code="1-new-new")
    result = sweep.sweep(root, timedelta(days=7))
    assert result.removed_codes == [] and (root / "codes" / "1-new-new").exists()
    outside = tmp_path / "etc"
    outside.mkdir()
    (root / "codes" / "9-bad-bad").write_text("../../etc\n")
    result = sweep.sweep(root, timedelta(days=7))
    assert result.removed_codes == ["9-bad-bad"]
    assert outside.exists() and (root / "codes" / "1-new-new").exists()


@pytest.mark.skipif(os.name != "posix", reason="symlinks")
def test_sweep_symlink_in_transfers_is_unlinked_not_followed(tmp_path):
    root = tmp_path / "srv"
    layout.init_layout(root)
    external = tmp_path / "external"
    external.mkdir()
    (external / "keep.txt").write_text("x")
    old = time.time() - 30 * 86400
    for p in (external, external / "keep.txt"):
        os.utime(p, (old, old))
    tid = "d" * 32
    (root / "transfers" / tid).symlink_to(external)
    result = sweep.sweep(root, timedelta(days=7))
    assert not (root / "transfers" / tid).is_symlink()
    assert (external / "keep.txt").exists()
    assert tid in result.removed_transfers


@pytest.mark.skipif(os.name != "posix" or os.geteuid() == 0, reason="needs non-root posix")
def test_sweep_reports_rmtree_failure(tmp_path):
    root = tmp_path / "srv"
    layout.init_layout(root)
    tid = "e" * 32
    d = make_transfer(root, tid, age=timedelta(days=30))
    sub = d / "sub"
    sub.mkdir()
    (sub / "f").write_text("x")
    old = time.time() - 30 * 86400
    for q in (sub / "f", sub, d):
        os.utime(q, (old, old))
    sub.chmod(0o500)
    try:
        result = sweep.sweep(root, timedelta(days=7))
        assert tid not in result.removed_transfers
        assert result.errors
    finally:
        sub.chmod(0o700)
