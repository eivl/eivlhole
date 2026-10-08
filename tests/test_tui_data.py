from datetime import UTC, date, datetime, timedelta

import pytest

from eivlhole.config import (
    Config,
    Defaults,
    HolidayConfig,
    HostConfig,
    JobConfig,
    PatternConfig,
    ScheduleConfig,
)
from eivlhole.errors import EivlholeError
from eivlhole.history import History
from eivlhole.tui import data


def make_config() -> Config:
    host = HostConfig(name="drop", hostname="10.0.0.5", user="eivlhole", port=2222)
    job = JobConfig(
        name="reports",
        remote_dir="/incoming/reports",
        files=["C:/r/summary.xlsx"],
        patterns=[PatternConfig(dir="C:/r/daily", regex=r"daily-\d{8}\.csv")],
        schedule=ScheduleConfig(time="07:00", days=["tue", "thu"]),
        holidays=HolidayConfig(country="NO", extra=["2026-12-24"]),
        on_success="move",
    )
    return Config(defaults=Defaults(host="drop"), hosts={"drop": host}, jobs={"reports": job})


def test_parse_history_filter_names_only():
    flt = data.parse_history_filter("daily-*.csv summary.xlsx")
    assert flt.names == ["daily-*.csv", "summary.xlsx"] and flt.on is None and flt.active


def test_parse_history_filter_with_date_and_range():
    flt = data.parse_history_filter("daily-*.csv 2026-10-07")
    assert flt.on == date(2026, 10, 7) and flt.names == ["daily-*.csv"]
    flt = data.parse_history_filter("2026-10-01..2026-10-07 x.csv")
    assert (flt.since, flt.until) == (date(2026, 10, 1), date(2026, 10, 7)) and flt.names == [
        "x.csv"
    ]
    flt = data.parse_history_filter("yesterday")
    assert flt.on == date.today() - timedelta(days=1) and flt.names == []


def test_parse_history_filter_bad_date():
    with pytest.raises(EivlholeError, match="YYYY-MM-DD"):
        data.parse_history_filter("x.csv 2026-13-40")


def test_filter_describe_and_empty():
    assert not data.parse_history_filter("").active
    assert data.HistoryFilter().describe() == "all runs"
    assert "daily-*.csv" in data.parse_history_filter("daily-*.csv 2026-10-07").describe()


def test_job_rows():
    rows = data.job_rows(make_config())
    assert rows[0] == data.JobRow(
        "reports", "drop", "/incoming/reports", "tue,thu", "07:00", "1 file, 1 pattern", "move"
    )


def test_host_rows_trusted_flag(tmp_path):
    cfg = make_config()
    kh = tmp_path / "known_hosts"
    rows = data.host_rows(cfg, kh)
    assert rows[0] == data.HostRow("drop", "10.0.0.5", 2222, "eivlhole", False, True)
    kh.write_text("[10.0.0.5]:2222 ssh-ed25519 AAAA\n")
    assert data.host_rows(cfg, kh)[0].trusted is True
    assert "pinned" in data.host_detail(cfg, "drop", kh)


def test_details(tmp_path):
    cfg = make_config()
    assert 'remote_dir = "/incoming/reports"' in data.job_detail(cfg, "reports")
    assert "10.0.0.5" in data.host_detail(
        cfg, "drop", tmp_path / "kh"
    ) and "not trusted" in data.host_detail(cfg, "drop", tmp_path / "kh")
    h = History(tmp_path / "h.sqlite")
    rid = h.start_run("job", job="reports", host="drop")
    h.add_file(
        rid,
        direction="out",
        filename="summary.xlsx",
        local_path="/l",
        remote_path="/r",
        size=20,
        sha256="bb",
        status="sent",
    )
    h.finish_run(rid, "ok")
    text = data.run_detail(h, rid)
    assert f"run {rid}" in text and "summary.xlsx" in text and "ok" in text
    rec = h.files_for_run(rid)[0]
    assert "sha256" in data.file_detail(rec) and "summary.xlsx" in data.file_detail(rec)
    assert data.run_detail(h, 999) == "no run with id 999"
    h.close()


def test_run_and_file_rows_use_filter(tmp_path):
    h = History(tmp_path / "h.sqlite")
    rid = h.start_run("job", job="reports", host="drop")
    h.add_file(
        rid,
        direction="out",
        filename="daily-20261007.csv",
        local_path="",
        remote_path="",
        size=1,
        sha256="",
        status="sent",
    )
    h.add_file(
        rid,
        direction="out",
        filename="summary.xlsx",
        local_path="",
        remote_path="",
        size=1,
        sha256="",
        status="sent",
    )
    h.finish_run(rid, "ok")
    assert [r.id for r in data.run_rows(h)] == [rid]
    assert [f.filename for f in data.file_rows(h, data.parse_history_filter("daily-*.csv"))] == [
        "daily-20261007.csv"
    ]
    assert len(data.file_rows(h, data.HistoryFilter())) == 2
    h.close()


def test_parse_history_filter_open_ended_ranges():
    flt = data.parse_history_filter("..2026-10-07")
    assert flt.since is None and flt.until == date(2026, 10, 7)
    flt = data.parse_history_filter("2026-10-01..")
    assert flt.since == date(2026, 10, 1) and flt.until is None
    with pytest.raises(EivlholeError, match="start..end"):
        data.parse_history_filter("..")


def test_file_rows_date_filters_use_history(tmp_path):
    h = History(tmp_path / "h.sqlite")
    rid = h.start_run("job", job="reports", host="drop")
    old_id = h.add_file(
        rid,
        direction="out",
        filename="old.csv",
        local_path="",
        remote_path="",
        size=1,
        sha256="",
        status="sent",
    )
    h.add_file(
        rid,
        direction="out",
        filename="new.csv",
        local_path="",
        remote_path="",
        size=1,
        sha256="",
        status="sent",
    )
    yesterday = datetime.now(UTC) - timedelta(days=1)
    h._conn.execute("UPDATE files SET ts=? WHERE id=?", (yesterday.isoformat(), old_id))
    h._conn.commit()
    assert [f.filename for f in data.file_rows(h, data.parse_history_filter("yesterday"))] == [
        "old.csv"
    ]
    assert [f.filename for f in data.file_rows(h, data.parse_history_filter("today"))] == [
        "new.csv"
    ]
    names = [f.filename for f in data.file_rows(h, data.parse_history_filter("2000-01-01..today"))]
    assert sorted(names) == ["new.csv", "old.csv"]
    h.close()


def test_job_rows_uses_default_host_when_job_host_is_none():
    cfg = make_config()
    assert cfg.jobs["reports"].host is None
    assert data.job_rows(cfg)[0].host == "drop"
