import shutil

from typer.testing import CliRunner

from eivlhole import paths
from eivlhole.cli.app import app
from eivlhole.config import load_config
from eivlhole.history import History

runner = CliRunner()


def setup(config_dir, ssh_server, client_key, tmp_path):
    (ssh_server.root / "incoming").mkdir()
    src = tmp_path / "out"
    src.mkdir()
    (src / "daily-20261008.csv").write_text("a")
    (src / "summary.xlsx").write_text("s")
    paths.config_file().write_text(
        "[defaults]\n"
        'host = "drop"\n\n'
        "[hosts.drop]\n"
        f'hostname = "{ssh_server.host}"\n'
        f"port = {ssh_server.port}\n"
        f'user = "{ssh_server.user}"\n'
        f'key = "{client_key.as_posix()}"\n\n'
        "[jobs.reports]\n"
        'remote_dir = "/incoming"\n'
        f'files = ["{(src / "summary.xlsx").as_posix()}"]\n'
        f'patterns = [{{ dir = "{src.as_posix()}", regex = "daily-\\\\d{{8}}\\\\.csv" }}]\n'
        'schedule = { time = "07:00", days = ["tue", "thu"] }\n'
        'holidays = { country = "NO", extra = ["2026-12-24"] }\n'
    )
    shutil.copy(ssh_server.known_hosts, paths.known_hosts_file())
    return src


def test_run_job_sends_and_exits_zero(config_dir, ssh_server, client_key, tmp_path):
    setup(config_dir, ssh_server, client_key, tmp_path)
    r = runner.invoke(app, ["run", "reports", "--date", "2026-10-08"])
    assert r.exit_code == 0, r.output
    assert "sent" in r.output and "daily-20261008.csv" in r.output
    assert (ssh_server.root / "incoming" / "summary.xlsx").exists()


def test_run_job_skipped_exits_two(config_dir, ssh_server, client_key, tmp_path):
    setup(config_dir, ssh_server, client_key, tmp_path)
    r = runner.invoke(app, ["run", "reports", "--date", "2026-04-03"])
    assert r.exit_code == 2 and "Good Friday" in r.output
    r = runner.invoke(app, ["run", "reports", "--date", "2026-04-03", "--force"])
    assert r.exit_code == 0


def test_run_job_dry_run(config_dir, ssh_server, client_key, tmp_path):
    setup(config_dir, ssh_server, client_key, tmp_path)
    r = runner.invoke(app, ["run", "reports", "--dry-run", "--date", "2026-10-08"])
    assert r.exit_code == 0 and "would send 2 file(s)" in r.output
    assert not (ssh_server.root / "incoming" / "summary.xlsx").exists()


def test_run_job_partial_exits_three(config_dir, ssh_server, client_key, tmp_path):
    src = setup(config_dir, ssh_server, client_key, tmp_path)
    (src / "summary.xlsx").unlink()
    r = runner.invoke(app, ["run", "reports", "--date", "2026-10-08"])
    assert r.exit_code == 3 and "no such file" in r.output


def test_run_unknown_job(config_dir, ssh_server, client_key, tmp_path):
    setup(config_dir, ssh_server, client_key, tmp_path)
    r = runner.invoke(app, ["run", "nope"])
    assert r.exit_code == 1 and "unknown job 'nope'" in r.output


def test_run_never_prompts_for_host_key(config_dir, ssh_server, client_key, tmp_path):
    setup(config_dir, ssh_server, client_key, tmp_path)
    paths.known_hosts_file().write_text("")
    r = runner.invoke(app, ["run", "reports", "--date", "2026-10-08"], input="y\n")
    assert r.exit_code == 1 and "hosts trust" in r.output


def test_jobs_add_list_show_remove(config_dir, ssh_server, client_key, tmp_path):
    setup(config_dir, ssh_server, client_key, tmp_path)
    r = runner.invoke(
        app,
        [
            "jobs",
            "add",
            "nightly",
            "--remote-dir",
            "/incoming/n",
            "--file",
            "C:/data/a.csv",
            "--pattern",
            "C:/data/daily::^d-\\d+\\.csv$",
            "--days",
            "mon,wed",
            "--time",
            "23:30",
            "--on-success",
            "move",
            "--retries",
            "5",
        ],
    )
    assert r.exit_code == 0, r.output
    job = load_config(paths.config_file()).jobs["nightly"]
    assert job.patterns[0].dir == "C:/data/daily" and job.patterns[0].regex == r"^d-\d+\.csv$"
    assert job.schedule.days == ["mon", "wed"] and job.schedule.time == "23:30"
    assert job.on_success == "move" and job.retries == 5
    r = runner.invoke(app, ["jobs", "list"])
    assert r.exit_code == 0 and "nightly" in r.output and "reports" in r.output
    r = runner.invoke(app, ["jobs", "show", "nightly"])
    assert r.exit_code == 0 and 'remote_dir = "/incoming/n"' in r.output
    r = runner.invoke(app, ["jobs", "remove", "nightly"])
    assert r.exit_code == 0 and "nightly" not in load_config(paths.config_file()).jobs


def test_jobs_add_rejects_bad_input(config_dir, ssh_server, client_key, tmp_path):
    setup(config_dir, ssh_server, client_key, tmp_path)
    r = runner.invoke(app, ["jobs", "add", "bad", "--remote-dir", "/x", "--days", "thuesday"])
    assert r.exit_code == 1 and 'unknown day "thuesday"' in r.output
    assert "bad" not in load_config(paths.config_file()).jobs
    r = runner.invoke(app, ["jobs", "add", "bad", "--remote-dir", "/x", "--pattern", "nocolons"])
    assert r.exit_code == 1 and "DIR::REGEX" in r.output
    r = runner.invoke(app, ["jobs", "add", "reports", "--remote-dir", "/x"])
    assert r.exit_code == 1 and "already exists" in r.output


def test_holidays_check_and_list(config_dir, ssh_server, client_key, tmp_path):
    setup(config_dir, ssh_server, client_key, tmp_path)
    r = runner.invoke(app, ["holidays", "check", "2026-04-03", "--job", "reports"])
    assert r.exit_code == 0 and "skip" in r.output and "Good Friday" in r.output
    r = runner.invoke(app, ["holidays", "check", "2026-10-08"])
    assert r.exit_code == 0 and "run" in r.output
    r = runner.invoke(app, ["holidays", "list", "--year", "2026", "--job", "reports"])
    assert r.exit_code == 0 and "Constitution Day" in r.output and "2026-12-24" in r.output
    r = runner.invoke(app, ["holidays", "check", "not-a-date"])
    assert r.exit_code == 1 and "YYYY-MM-DD" in r.output


def test_run_unknown_job_records_failed_run(config_dir, ssh_server, client_key, tmp_path):
    setup(config_dir, ssh_server, client_key, tmp_path)
    r = runner.invoke(app, ["run", "nope"])
    assert r.exit_code == 1
    with History(paths.history_file()) as hist:
        runs = hist.runs()
    assert len(runs) == 1
    run = runs[0]
    assert run.kind == "job" and run.job == "nope" and run.status == "failed"
    assert run.exit_code == 1 and "unknown job" in run.error


def test_run_bad_config_records_failed_run(config_dir, ssh_server, client_key, tmp_path):
    paths.config_file().write_text("[jobs.reports\n")
    r = runner.invoke(app, ["run", "reports"])
    assert r.exit_code == 1
    with History(paths.history_file()) as hist:
        runs = hist.runs()
    assert len(runs) == 1 and runs[0].job == "reports" and runs[0].status == "failed"


def test_run_job_failure_writes_one_run(config_dir, ssh_server, client_key, tmp_path):
    setup(config_dir, ssh_server, client_key, tmp_path)
    paths.known_hosts_file().write_text("")
    r = runner.invoke(app, ["run", "reports", "--date", "2026-10-08"])
    assert r.exit_code == 1
    with History(paths.history_file()) as hist:
        assert len(hist.runs()) == 1
