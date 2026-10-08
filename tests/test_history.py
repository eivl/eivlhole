import sqlite3
from datetime import UTC, date, datetime, timedelta

import pytest

from eivlhole.history import History

_opened: list[History] = []


@pytest.fixture(autouse=True)
def _close_histories():
    yield
    while _opened:
        _opened.pop().close()


def make(tmp_path):
    h = History(tmp_path / "h.sqlite")
    _opened.append(h)
    return h


def test_run_lifecycle(tmp_path):
    h = make(tmp_path)
    rid = h.start_run("send", host="drop")
    h.finish_run(rid, "ok", exit_code=0)
    rec = h.run(rid)
    assert rec is not None
    assert rec.kind == "send" and rec.host == "drop" and rec.status == "ok"
    assert rec.finished is not None and rec.machine and rec.username
    h.close()


def test_add_and_query_files_by_name_and_glob(tmp_path):
    h = make(tmp_path)
    rid = h.start_run("job", job="weekly")
    h.add_file(
        rid,
        direction="out",
        filename="daily-20261007.csv",
        local_path="/l/a",
        remote_path="/r/a",
        size=10,
        sha256="aa",
        status="sent",
    )
    h.add_file(
        rid,
        direction="out",
        filename="summary.xlsx",
        local_path="/l/b",
        remote_path="/r/b",
        size=20,
        sha256="bb",
        status="sent",
    )
    h.finish_run(rid, "ok")
    assert [f.filename for f in h.files(["summary.xlsx"])] == ["summary.xlsx"]
    assert [f.filename for f in h.files(["daily-*.csv"])] == ["daily-20261007.csv"]
    assert len(h.files(["daily-*.csv", "summary.xlsx"])) == 2
    assert len(h.files()) == 2
    assert h.files(["nothing"]) == []


def test_files_filters(tmp_path):
    h = make(tmp_path)
    r1 = h.start_run("job", job="a", host="h1")
    h.add_file(
        r1,
        direction="out",
        filename="x",
        local_path="",
        remote_path="",
        size=1,
        sha256="",
        status="sent",
    )
    r2 = h.start_run("receive", host="h2")
    h.add_file(
        r2,
        direction="in",
        filename="x",
        local_path="",
        remote_path="",
        size=1,
        sha256="",
        status="failed",
        error="boom",
    )
    assert len(h.files(["x"])) == 2
    assert len(h.files(["x"], job="a")) == 1
    assert len(h.files(["x"], host="h2")) == 1
    assert len(h.files(["x"], status="failed")) == 1
    assert h.files(["x"], limit=1)[0].run_id == r2  # newest first


def test_files_by_date(tmp_path):
    h = make(tmp_path)
    rid = h.start_run("job", job="a")
    fid = h.add_file(
        rid,
        direction="out",
        filename="x",
        local_path="",
        remote_path="",
        size=1,
        sha256="",
        status="sent",
    )
    yesterday = datetime.now(UTC) - timedelta(days=1)
    h._conn.execute("UPDATE files SET ts=? WHERE id=?", (yesterday.isoformat(), fid))
    h._conn.commit()
    today = date.today()
    assert h.files(["x"], on=today) == []
    assert len(h.files(["x"], on=today - timedelta(days=1))) == 1
    assert len(h.files(["x"], since=today - timedelta(days=2), until=today)) == 1
    assert h.files(["x"], since=today) == []


def test_runs_listing_and_files_for_run(tmp_path):
    h = make(tmp_path)
    r1 = h.start_run("job", job="a")
    h.finish_run(r1, "ok")
    r2 = h.start_run("job", job="b")
    h.add_file(
        r2,
        direction="out",
        filename="f",
        local_path="",
        remote_path="",
        size=1,
        sha256="",
        status="sent",
    )
    h.finish_run(r2, "failed", exit_code=1, error="x")
    runs = h.runs()
    assert [r.id for r in runs] == [r2, r1]
    assert [r.id for r in h.runs(job="a")] == [r1]
    assert [r.id for r in h.runs(status="failed")] == [r2]
    assert [f.filename for f in h.files_for_run(r2)] == ["f"]
    assert h.run(999) is None


def test_reopen_keeps_data(tmp_path):
    h = make(tmp_path)
    rid = h.start_run("send")
    h.close()
    h2 = make(tmp_path)
    assert h2.run(rid) is not None
    h2.close()


def test_files_exact_name_with_brackets(tmp_path):
    h = make(tmp_path)
    rid = h.start_run("job", job="a")
    for name in ("report[1].csv", "report1.csv"):
        h.add_file(
            rid,
            direction="out",
            filename=name,
            local_path="",
            remote_path="",
            size=1,
            sha256="",
            status="sent",
        )
    assert [f.filename for f in h.files(["report[1].csv"])] == ["report[1].csv"]
    assert [f.filename for f in h.files(["report1.csv"])] == ["report1.csv"]
    assert len(h.files(["report*.csv"])) == 2
    assert [f.filename for f in h.files(["report?.csv"])] == ["report1.csv"]
    h.close()


def test_update_file_error(tmp_path):
    h = make(tmp_path)
    rid = h.start_run("job", job="j")
    fid = h.add_file(
        rid,
        direction="out",
        filename="a.csv",
        local_path="/l/a",
        remote_path="/r/a",
        size=1,
        sha256="aa",
        status="sent",
    )
    h.update_file_error(fid, "sent, but on_success 'move' failed: locked")
    (rec,) = h.files_for_run(rid)
    assert rec.status == "sent" and "on_success" in rec.error


def test_corrupt_database_does_not_leak_connection(tmp_path):
    path = tmp_path / "bad.sqlite"
    path.write_bytes(b"not a database")
    with pytest.raises(sqlite3.DatabaseError):
        History(path)
