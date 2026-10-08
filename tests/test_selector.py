import os
from pathlib import Path

import pytest

from eivlhole.config import JobConfig, PatternConfig
from eivlhole.jobs.selector import select_files


def make_tree(tmp_path: Path) -> Path:
    d = tmp_path / "daily"
    (d / "sub").mkdir(parents=True)
    (d / "daily-20261006.csv").write_text("1")
    (d / "daily-20261007.csv").write_text("2")
    (d / "notes.txt").write_text("n")
    (d / "sub" / "daily-20261001.csv").write_text("old")
    (tmp_path / "summary.xlsx").write_text("s")
    return d


def test_exact_files_and_patterns(tmp_path):
    d = make_tree(tmp_path)
    job = JobConfig(
        name="j",
        remote_dir="/x",
        files=[str(tmp_path / "summary.xlsx")],
        patterns=[PatternConfig(dir=str(d), regex=r"daily-\d{8}\.csv")],
    )
    sel = select_files(job)
    assert [p.name for p in sel.files] == [
        "daily-20261006.csv",
        "daily-20261007.csv",
        "summary.xlsx",
    ]
    assert sel.missing == [] and sel.warnings == []


def test_recursive_pattern(tmp_path):
    d = make_tree(tmp_path)
    job = JobConfig(
        name="j",
        remote_dir="/x",
        patterns=[PatternConfig(dir=str(d), regex=r"daily-.*\.csv", recursive=True)],
    )
    assert [p.name for p in select_files(job).files] == [
        "daily-20261001.csv",
        "daily-20261006.csv",
        "daily-20261007.csv",
    ]


def test_regex_is_fullmatch_on_name_only(tmp_path):
    d = make_tree(tmp_path)
    job = JobConfig(name="j", remote_dir="/x", patterns=[PatternConfig(dir=str(d), regex=r"daily")])
    sel = select_files(job)
    assert sel.files == [] and any("matched no files" in w for w in sel.warnings)


def test_missing_exact_file_is_reported(tmp_path):
    job = JobConfig(name="j", remote_dir="/x", files=[str(tmp_path / "ghost.csv")])
    sel = select_files(job)
    assert sel.files == [] and sel.missing == [str(tmp_path / "ghost.csv")]


def test_missing_pattern_dir_warns_and_continues(tmp_path):
    (tmp_path / "a.txt").write_text("a")
    job = JobConfig(
        name="j",
        remote_dir="/x",
        files=[str(tmp_path / "a.txt")],
        patterns=[PatternConfig(dir=str(tmp_path / "nope"), regex=r".*")],
    )
    sel = select_files(job)
    assert [p.name for p in sel.files] == ["a.txt"]
    assert len(sel.warnings) == 1 and "does not exist" in sel.warnings[0]


def test_dedupes_overlapping_sources(tmp_path):
    d = make_tree(tmp_path)
    job = JobConfig(
        name="j",
        remote_dir="/x",
        files=[str(d / "daily-20261007.csv")],
        patterns=[PatternConfig(dir=str(d), regex=r"daily-\d{8}\.csv")],
    )
    assert [p.name for p in select_files(job).files] == ["daily-20261006.csv", "daily-20261007.csv"]


def test_expands_user_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    (tmp_path / "r.txt").write_text("r")
    job = JobConfig(name="j", remote_dir="/x", files=["~/r.txt"])
    assert select_files(job).files == [tmp_path / "r.txt"]


def test_directories_are_not_selected(tmp_path):
    d = make_tree(tmp_path)
    job = JobConfig(name="j", remote_dir="/x", patterns=[PatternConfig(dir=str(d), regex=r"sub")])
    sel = select_files(job)
    assert sel.files == []


@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0, reason="root ignores directory modes"
)
def test_unreadable_pattern_dir_warns(tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir()
    (locked / "daily-1.csv").write_text("x")
    (tmp_path / "keep.txt").write_text("k")
    locked.chmod(0o000)
    try:
        job = JobConfig(
            name="j",
            remote_dir="/x",
            files=[str(tmp_path / "keep.txt")],
            patterns=[PatternConfig(dir=str(locked), regex=r".*\.csv")],
        )
        sel = select_files(job)
        assert [p.name for p in sel.files] == ["keep.txt"]
        assert len(sel.warnings) == 1 and "cannot read pattern directory" in sel.warnings[0]
    finally:
        locked.chmod(0o700)


def test_recursive_skips_sent_dir(tmp_path):
    d = make_tree(tmp_path)
    (d / "sent").mkdir()
    (d / "sent" / "daily.csv").write_text("delivered")
    (d / "sub" / "sent").mkdir()
    (d / "sub" / "sent" / "daily-old.csv").write_text("delivered")
    job = JobConfig(
        name="j",
        remote_dir="/x",
        patterns=[PatternConfig(dir=str(d), regex=r"daily.*\.csv", recursive=True)],
    )
    sel = select_files(job)
    assert [p.name for p in sel.files] == [
        "daily-20261001.csv",
        "daily-20261006.csv",
        "daily-20261007.csv",
    ]
    assert all("sent" not in p.parts for p in sel.files)


@pytest.mark.skipif(
    os.name != "posix" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="needs POSIX permissions and a non-root user",
)
def test_recursive_unreadable_subdir_keeps_other_matches(tmp_path):
    d = make_tree(tmp_path)
    locked = d / "locked"
    locked.mkdir()
    (locked / "daily-20261002.csv").write_text("x")
    locked.chmod(0o000)
    try:
        job = JobConfig(
            name="j",
            remote_dir="/x",
            patterns=[PatternConfig(dir=str(d), regex=r"daily-.*\.csv", recursive=True)],
        )
        sel = select_files(job)
    finally:
        locked.chmod(0o700)
    assert [p.name for p in sel.files] == [
        "daily-20261001.csv",
        "daily-20261006.csv",
        "daily-20261007.csv",
    ]
    assert len(sel.warnings) == 1
    assert sel.warnings[0].startswith(f"cannot read directory {locked}")


@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0, reason="root ignores directory modes"
)
def test_recursive_unreadable_pattern_dir_warns(tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir()
    (locked / "daily-1.csv").write_text("x")
    locked.chmod(0o000)
    try:
        job = JobConfig(
            name="j",
            remote_dir="/x",
            patterns=[PatternConfig(dir=str(locked), regex=r".*\.csv", recursive=True)],
        )
        sel = select_files(job)
    finally:
        locked.chmod(0o700)
    assert sel.files == []
    assert len(sel.warnings) == 1 and "cannot read pattern directory" in sel.warnings[0]
