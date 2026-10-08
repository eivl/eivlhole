import json
from datetime import UTC, datetime, timedelta

from typer.testing import CliRunner

from eivlhole import paths
from eivlhole.cli.app import app
from eivlhole.history import History

runner = CliRunner()


def seed(config_dir):
    h = History(paths.history_file())
    r1 = h.start_run("job", job="reports", host="drop")
    f1 = h.add_file(
        r1,
        direction="out",
        filename="daily-20261007.csv",
        local_path="/l/d",
        remote_path="/incoming/daily-20261007.csv",
        size=10,
        sha256="aa",
        status="sent",
    )
    h.add_file(
        r1,
        direction="out",
        filename="summary.xlsx",
        local_path="/l/s",
        remote_path="/incoming/summary.xlsx",
        size=20,
        sha256="bb",
        status="sent",
    )
    h.finish_run(r1, "ok")
    r2 = h.start_run("receive", host="drop")
    h.add_file(
        r2,
        direction="in",
        filename="report[1].csv",
        local_path="/in/r",
        remote_path="/x",
        size=5,
        sha256="cc",
        status="failed",
        error="checksum mismatch",
    )
    h.finish_run(r2, "failed", exit_code=1, error="checksum mismatch")
    yesterday = (datetime.now(UTC) - timedelta(days=1)).isoformat(timespec="seconds")
    h._conn.execute("UPDATE files SET ts=? WHERE id=?", (yesterday, f1))
    h._conn.execute("UPDATE runs SET started=? WHERE id=?", (yesterday, r1))
    h._conn.commit()
    h.close()
    return r1, r2


def test_history_files_glob_and_yesterday(config_dir):
    seed(config_dir)
    r = runner.invoke(app, ["history", "files", "daily-*.csv", "--on", "yesterday"])
    assert r.exit_code == 0 and "daily-20261007.csv" in r.output and "summary" not in r.output
    r = runner.invoke(app, ["history", "files", "daily-*.csv", "--on", "today"])
    assert r.exit_code == 0 and "daily-20261007.csv" not in r.output


def test_history_files_filters_and_json(config_dir):
    r1, r2 = seed(config_dir)
    r = runner.invoke(app, ["history", "files", "summary.xlsx", "report[1].csv", "--json"])
    assert r.exit_code == 0
    rows = [json.loads(line) for line in r.output.strip().splitlines()]
    assert {row["filename"] for row in rows} == {"summary.xlsx", "report[1].csv"}
    assert all("ts" in row and "run_id" in row for row in rows)
    r = runner.invoke(app, ["history", "files", "--status", "failed", "--json"])
    assert [json.loads(line)["filename"] for line in r.output.strip().splitlines()] == [
        "report[1].csv"
    ]
    r = runner.invoke(
        app, ["history", "files", "--job", "reports", "--host", "drop", "-n", "1", "--json"]
    )
    assert len(r.output.strip().splitlines()) == 1


def test_history_files_no_match_is_exit_zero(config_dir):
    seed(config_dir)
    r = runner.invoke(app, ["history", "files", "nothing.bin"])
    assert r.exit_code == 0 and "no matching files" in r.output


def test_history_runs_and_show(config_dir):
    r1, r2 = seed(config_dir)
    r = runner.invoke(app, ["history", "runs"])
    assert r.exit_code == 0 and "reports" in r.output and "receive" in r.output
    r = runner.invoke(app, ["history", "runs", "--status", "failed", "--json"])
    rows = [json.loads(line) for line in r.output.strip().splitlines()]
    assert [row["id"] for row in rows] == [r2]
    r = runner.invoke(app, ["history", "runs", "--since", "today", "--json"])
    assert [json.loads(line)["id"] for line in r.output.strip().splitlines()] == [r2]
    r = runner.invoke(app, ["history", "show", str(r1)])
    assert r.exit_code == 0 and "summary.xlsx" in r.output and "daily-20261007.csv" in r.output
    r = runner.invoke(app, ["history", "show", str(r1), "--json"])
    doc = json.loads(r.output)
    assert doc["run"]["id"] == r1 and len(doc["files"]) == 2
    r = runner.invoke(app, ["history", "show", "999"])
    assert r.exit_code == 1 and "no run with id 999" in r.output


def test_history_bad_date(config_dir):
    seed(config_dir)
    r = runner.invoke(app, ["history", "files", "x", "--on", "2026-13-40"])
    assert r.exit_code == 1 and "YYYY-MM-DD" in r.output


def test_history_unavailable_db(config_dir):
    paths.history_file().mkdir()
    r = runner.invoke(app, ["history", "runs"])
    assert r.exit_code == 1 and "history unavailable" in r.output
