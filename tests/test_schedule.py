import os
import subprocess
from datetime import date
from pathlib import Path

import pytest

from eivlhole.config import JobConfig, ScheduleConfig
from eivlhole.errors import EivlholeError
from eivlhole.schedule import base, linux, windows

JOB = JobConfig(
    name="reports",
    remote_dir="/incoming",
    schedule=ScheduleConfig(time="07:00", days=["tue", "thu"]),
)
CMD = ["C:\\Tools\\eivlhole.exe"]


def spec_for(job=JOB, command=CMD, config_dir="C:\\Users\\me\\AppData\\Roaming\\eivlhole"):
    # config_dir is a plain string so Windows-style paths survive on a Linux test runner
    return base.ScheduleSpec(
        job=job.name,
        time=job.schedule.time,
        days=list(job.schedule.days),
        command=command + ["--config-dir", config_dir, "--unattended", "run", job.name],
    )


class FakeRun:
    def __init__(self, returncode=0, stdout="", raise_missing=False, stderr=""):
        self.returncode, self.stdout, self.raise_missing = returncode, stdout, raise_missing
        self.stderr = stderr
        self.calls: list[tuple[list[str], dict]] = []
        self.stdin: list[str] = []

    def __call__(self, args, **kwargs):
        self.calls.append((list(args), kwargs))
        if self.raise_missing:
            raise FileNotFoundError(args[0])
        if "input" in kwargs:
            self.stdin.append(kwargs["input"])
        return subprocess.CompletedProcess(
            args, self.returncode, stdout=self.stdout, stderr=self.stderr
        )


def test_build_spec_uses_config_dir_and_unattended(monkeypatch):
    monkeypatch.setattr(base, "resolve_command", lambda: ["/usr/local/bin/eivlhole"])
    spec = base.build_spec(JOB, Path("/home/me/.config/eivlhole"))
    assert spec.command == [
        "/usr/local/bin/eivlhole",
        "--config-dir",
        "/home/me/.config/eivlhole",
        "--unattended",
        "run",
        "reports",
    ]
    assert spec.days == ["tue", "thu"] and spec.time == "07:00"
    dup = JobConfig(
        name="reports",
        remote_dir="/incoming",
        schedule=ScheduleConfig(time="07:00", days=["tue", "tue", "thu"]),
    )
    assert base.build_spec(dup, Path("/c")).days == ["tue", "thu"]


def test_build_spec_requires_days():
    job = JobConfig(name="j", remote_dir="/x")
    with pytest.raises(EivlholeError, match="schedule.days"):
        base.build_spec(job, Path("/c"))


def test_resolve_command_falls_back_to_module(monkeypatch):
    monkeypatch.setattr(base.shutil, "which", lambda name: None)
    cmd = base.resolve_command()
    assert cmd[-2:] == ["-m", "eivlhole"] and cmd[0].endswith(
        ("python", "python3", "python.exe", "python3.13")
    )


def test_resolve_command_is_absolute(monkeypatch):
    monkeypatch.setattr(base.shutil, "which", lambda name: "rel/eivlhole")
    cmd = base.resolve_command()
    assert os.path.isabs(cmd[0]) and cmd[0].endswith("eivlhole")


def test_build_spec_resolves_relative_config_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(base, "resolve_command", lambda: ["/usr/local/bin/eivlhole"])
    monkeypatch.chdir(tmp_path)
    spec = base.build_spec(JOB, Path("cfg"))
    assert str((tmp_path / "cfg").resolve()) in spec.command


def test_windows_xml_golden():
    xml = windows.TaskScheduler(start_date=date(2026, 10, 8)).render(spec_for())
    assert xml.startswith('<?xml version="1.0" encoding="UTF-16"?>')
    assert "<StartBoundary>2026-10-08T07:00:00</StartBoundary>" in xml
    assert "<Tuesday />" in xml and "<Thursday />" in xml and "<Monday />" not in xml
    assert "<StartWhenAvailable>true</StartWhenAvailable>" in xml
    assert "<LogonType>S4U</LogonType>" in xml
    assert "<Command>C:\\Tools\\eivlhole.exe</Command>" in xml
    assert (
        "<Arguments>--config-dir C:\\Users\\me\\AppData\\Roaming\\eivlhole "
        "--unattended run reports</Arguments>"
    ) in xml
    assert "<Description>eivlhole job reports</Description>" in xml


def test_windows_xml_escapes_special_characters():
    spec = spec_for(command=["C:\\Program Files\\eivlhole.exe"], config_dir="C:\\My Dir\\a&b")
    xml = windows.TaskScheduler(start_date=date(2026, 10, 8)).render(spec)
    assert "<Command>C:\\Program Files\\eivlhole.exe</Command>" in xml
    assert (
        "--config-dir &quot;C:\\My Dir\\a&amp;b&quot;" in xml
    )  # list2cmdline quotes, escape() encodes


def test_windows_install_writes_xml_and_calls_schtasks(config_dir, monkeypatch):
    run = FakeRun()
    monkeypatch.setattr(base, "default_run", run)
    sched = windows.TaskScheduler(start_date=date(2026, 10, 8))
    result = sched.install(spec_for())
    assert result.ok, result.output
    xml_file = config_dir / "schedule" / "reports.xml"
    assert xml_file.read_bytes().startswith(b"\xff\xfe")  # UTF-16 LE BOM
    args = run.calls[0][0]
    assert args[:3] == ["schtasks", "/Create", "/TN"] and args[3] == "eivlhole\\reports"
    assert "/XML" in args and str(xml_file) in args and "/F" in args


def test_windows_install_failure_returns_manual(config_dir, monkeypatch):
    run = FakeRun(returncode=1, stdout="ERROR: Access is denied.")
    monkeypatch.setattr(base, "default_run", run)
    result = windows.TaskScheduler(start_date=date(2026, 10, 8)).install(spec_for())
    assert not result.ok and "Access is denied" in result.output
    assert "schtasks /Create /TN eivlhole\\reports /XML" in result.manual
    assert "S4U" in result.manual and "local disks" in result.manual
    assert str(config_dir / "schedule" / "reports.xml") in result.manual


def test_install_without_scheduler_binary_returns_manual(config_dir, monkeypatch):
    run = FakeRun(raise_missing=True)
    monkeypatch.setattr(base, "default_run", run)
    result = windows.TaskScheduler(start_date=date(2026, 10, 8)).install(spec_for())
    assert not result.ok and "not found" in result.output and "schtasks" in result.manual
    result = linux.CronScheduler().install(spec_for(command=["/usr/local/bin/eivlhole"]))
    assert not result.ok and "not found" in result.output and "crontab" in result.manual


def test_windows_status_and_uninstall(monkeypatch):
    run = FakeRun(stdout="TaskName: \\eivlhole\\reports\nStatus: Ready\n")
    monkeypatch.setattr(base, "default_run", run)
    sched = windows.TaskScheduler()
    assert "Ready" in sched.status("reports")
    assert sched.uninstall("reports").ok
    assert run.calls[-1][0][:4] == ["schtasks", "/Delete", "/TN", "eivlhole\\reports"]
    run.returncode = 1
    assert sched.status("reports") is None


def test_cron_line_golden():
    line = linux.CronScheduler().render(
        spec_for(command=["/usr/local/bin/eivlhole"], config_dir="/home/me/.config/eivlhole")
    )
    assert line == (
        "0 7 * * 2,4 /usr/local/bin/eivlhole --config-dir /home/me/.config/eivlhole "
        "--unattended run reports  # eivlhole:reports"
    )


def test_cron_line_quotes_spaces():
    line = linux.CronScheduler().render(
        spec_for(command=["/opt/my tools/eivlhole"], config_dir="/home/me/.config/eivlhole")
    )
    assert "'/opt/my tools/eivlhole'" in line


def test_cron_install_replaces_existing_and_keeps_others(monkeypatch):
    run = FakeRun(
        stdout="MAILTO=me\n0 1 * * * /bin/old --unattended run reports  # eivlhole:reports\n"
        "30 2 * * * backup.sh\n"
    )
    monkeypatch.setattr(base, "default_run", run)
    sched = linux.CronScheduler()
    spec = spec_for(command=["/usr/local/bin/eivlhole"], config_dir="/c")
    assert sched.install(spec).ok
    written = run.stdin[-1]
    assert written.count("# eivlhole:reports") == 1 and "/bin/old" not in written
    assert "MAILTO=me" in written and "backup.sh" in written
    assert written.endswith("\n") and sched.render(spec) in written
    assert run.calls[0][0] == ["crontab", "-l"] and run.calls[-1][0] == ["crontab", "-"]


def test_cron_no_existing_crontab(monkeypatch):
    run = FakeRun(returncode=1, stdout="", stderr="no crontab for me")
    monkeypatch.setattr(base, "default_run", run)
    text = linux.read_crontab()
    assert text == ""


def test_cron_status_and_uninstall(monkeypatch):
    run = FakeRun(stdout="0 7 * * 2,4 /x --unattended run reports  # eivlhole:reports\n")
    monkeypatch.setattr(base, "default_run", run)
    sched = linux.CronScheduler()
    assert sched.status("reports").startswith("0 7 * * 2,4")
    assert sched.status("other") is None
    assert sched.uninstall("reports").ok
    assert "eivlhole:reports" not in run.stdin[-1]


def test_get_scheduler_by_platform():
    assert isinstance(base.get_scheduler("windows"), windows.TaskScheduler)
    assert isinstance(base.get_scheduler("linux"), linux.CronScheduler)
    assert base.get_scheduler().name in ("Task Scheduler", "cron")


def test_cron_read_error_does_not_write(monkeypatch):
    run = FakeRun(returncode=2, stderr="crontab: permission denied")
    monkeypatch.setattr(base, "default_run", run)
    result = linux.CronScheduler().install(spec_for(command=["/usr/local/bin/eivlhole"]))
    assert not result.ok and "could not read" in result.output
    assert ["crontab", "-"] not in [c[0] for c in run.calls]
    assert not linux.CronScheduler().uninstall("reports").ok
    assert ["crontab", "-"] not in [c[0] for c in run.calls]


def test_cron_stderr_not_written_back(monkeypatch):
    run = FakeRun(stdout="30 2 * * * backup.sh\n", stderr="warning: something")
    monkeypatch.setattr(base, "default_run", run)
    assert linux.CronScheduler().install(spec_for(command=["/usr/local/bin/eivlhole"])).ok
    assert "warning:" not in run.stdin[-1] and "backup.sh" in run.stdin[-1]


def test_cron_line_escapes_percent():
    line = linux.CronScheduler().render(
        spec_for(command=["/opt/100%tools/eivlhole"], config_dir="/c")
    )
    assert "100\\%tools" in line
    assert line.replace("\\%", "").count("%") == 0
