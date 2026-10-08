import pytest

from eivlhole.config import (
    Config,
    HostConfig,
    JobConfig,
    PatternConfig,
    load_config,
    parse_config,
    save_config,
    to_dict,
)
from eivlhole.errors import ConfigError

FULL = {
    "defaults": {"host": "drop", "drop_root": "/srv/eivlhole", "code_words": 3, "receive_dir": "."},
    "hosts": {"drop": {"hostname": "drop.corp.local", "port": 22, "user": "eivlhole"}},
    "jobs": {
        "weekly-reports": {
            "host": "drop",
            "remote_dir": "/incoming/reports",
            "files": ["C:/reports/summary.xlsx"],
            "patterns": [
                {"dir": "C:/reports/daily", "regex": r"^daily-\d{8}\.csv$", "recursive": False}
            ],  # noqa: E501
            "schedule": {"time": "07:00", "days": ["tue", "thu"]},
            "holidays": {"country": "NO", "extra": ["2026-12-24", "2026-07-06..2026-07-24"]},
            "on_success": "keep",
            "retries": 3,
        }
    },
}


def test_parse_full_config():
    cfg = parse_config(FULL)
    assert cfg.defaults.host == "drop"
    assert cfg.hosts["drop"] == HostConfig(name="drop", hostname="drop.corp.local", user="eivlhole")
    job = cfg.jobs["weekly-reports"]
    assert job.remote_dir == "/incoming/reports"
    assert job.patterns == [PatternConfig(dir="C:/reports/daily", regex=r"^daily-\d{8}\.csv$")]
    assert job.schedule.days == ["tue", "thu"]
    assert job.holidays.extra[1] == "2026-07-06..2026-07-24"


def test_round_trip(tmp_path):
    cfg = parse_config(FULL)
    path = tmp_path / "config.toml"
    save_config(cfg, path)
    assert load_config(path) == cfg
    assert to_dict(cfg)["jobs"]["weekly-reports"]["patterns"][0]["recursive"] is False


def test_missing_file_is_empty_config(tmp_path):
    assert load_config(tmp_path / "nope.toml") == Config()


def test_host_for_falls_back_to_default():
    cfg = parse_config(FULL)
    assert cfg.host_for(None).name == "drop"
    assert cfg.host_for("drop").hostname == "drop.corp.local"


def test_host_for_unknown_raises():
    cfg = parse_config(FULL)
    with pytest.raises(ConfigError, match="unknown host 'nope'"):
        cfg.host_for("nope")
    with pytest.raises(ConfigError, match="no host given and defaults.host is not set"):
        Config().host_for(None)


def test_job_without_host_and_no_default_is_error():
    data = {"hosts": {"a": {"hostname": "h", "user": "u"}}, "jobs": {"j": {"remote_dir": "/x"}}}
    with pytest.raises(ConfigError, match="jobs.j.host: required because defaults.host is not set"):
        parse_config(data)


def test_job_without_host_uses_default_when_set():
    data = {
        "defaults": {"host": "a"},
        "hosts": {"a": {"hostname": "h", "user": "u"}},
        "jobs": {"j": {"remote_dir": "/x"}},
    }
    cfg = parse_config(data)
    assert cfg.jobs["j"].host is None and cfg.host_for(cfg.jobs["j"].host).name == "a"


def test_time_and_date_reject_trailing_newline():
    import copy

    data = copy.deepcopy(FULL)
    data["jobs"]["weekly-reports"]["schedule"]["time"] = "07:00\n"
    data["jobs"]["weekly-reports"]["holidays"]["extra"] = ["2026-12-24\n"]
    with pytest.raises(ConfigError) as info:
        parse_config(data)
    assert len(info.value.problems) == 2


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda d: d["hosts"]["drop"].pop("hostname"), "hosts.drop.hostname: required"),
        (lambda d: d["hosts"]["drop"].update(port="x"), "hosts.drop.port: must be an integer"),
        (
            lambda d: d["jobs"]["weekly-reports"].pop("remote_dir"),
            "jobs.weekly-reports.remote_dir: required",
        ),  # noqa: E501
        (
            lambda d: d["jobs"]["weekly-reports"].update(host="ghost"),
            "jobs.weekly-reports.host: unknown host 'ghost'",
        ),  # noqa: E501
        (
            lambda d: d["jobs"]["weekly-reports"]["schedule"].update(days=["thuesday"]),
            'jobs.weekly-reports.schedule.days: unknown day "thuesday"',
        ),  # noqa: E501
        (
            lambda d: d["jobs"]["weekly-reports"]["schedule"].update(time="7am"),
            "jobs.weekly-reports.schedule.time: must be HH:MM",
        ),  # noqa: E501
        (
            lambda d: d["jobs"]["weekly-reports"].update(on_success="burn"),
            "jobs.weekly-reports.on_success: must be one of keep, move, delete",
        ),  # noqa: E501
        (
            lambda d: d["jobs"]["weekly-reports"]["patterns"][0].update(regex="(["),
            "jobs.weekly-reports.patterns[0].regex: invalid regex",
        ),  # noqa: E501
        (
            lambda d: d["jobs"]["weekly-reports"]["holidays"].update(extra=["xmas"]),
            "jobs.weekly-reports.holidays.extra[0]: must be YYYY-MM-DD or YYYY-MM-DD..YYYY-MM-DD",
        ),  # noqa: E501
        (
            lambda d: d["jobs"]["weekly-reports"].update(retries=-1),
            "jobs.weekly-reports.retries: must be >= 0",
        ),  # noqa: E501
        (
            lambda d: d["defaults"].update(code_words=0),
            "defaults.code_words: must be between 1 and 8",
        ),  # noqa: E501
        (lambda d: d.update(bogus={}), "bogus: unknown section"),
    ],
)
def test_validation_errors(mutate, message):
    import copy

    data = copy.deepcopy(FULL)
    mutate(data)
    with pytest.raises(ConfigError) as info:
        parse_config(data)
    assert message in str(info.value)


def test_all_problems_reported_at_once():
    import copy

    data = copy.deepcopy(FULL)
    data["hosts"]["drop"].pop("hostname")
    data["jobs"]["weekly-reports"].update(on_success="burn")
    with pytest.raises(ConfigError) as info:
        parse_config(data)
    assert len(info.value.problems) == 2


def test_job_config_defaults():
    job = JobConfig(name="j", remote_dir="/x")
    assert job.schedule.days == []
    assert job.holidays.country == "NO"
    assert job.retries == 3


def _job_problems(mutate):
    import copy

    data = copy.deepcopy(FULL)
    mutate(data)
    with pytest.raises(ConfigError) as info:
        parse_config(data)
    return info.value.problems


def test_patterns_not_a_list_int():
    problems = _job_problems(lambda d: d["jobs"]["weekly-reports"].update(patterns=5))
    assert problems == ["jobs.weekly-reports.patterns: must be a list of tables"]


def test_patterns_not_a_list_string():
    problems = _job_problems(lambda d: d["jobs"]["weekly-reports"].update(patterns="abc"))
    assert problems == ["jobs.weekly-reports.patterns: must be a list of tables"]


def test_unknown_key_in_job():
    problems = _job_problems(lambda d: d["jobs"]["weekly-reports"].update(on_succes="delete"))
    assert problems == ["jobs.weekly-reports.on_succes: unknown key"]


def test_unknown_key_in_host_and_pattern():
    def mutate(d):
        d["hosts"]["drop"]["prot"] = 22
        d["jobs"]["weekly-reports"]["patterns"][0]["recursve"] = True

    problems = _job_problems(mutate)
    assert "hosts.drop.prot: unknown key" in problems
    assert "jobs.weekly-reports.patterns[0].recursve: unknown key" in problems
    assert len(problems) == 2


def test_unknown_key_in_defaults_schedule_holidays():
    def mutate(d):
        d["defaults"]["bogus"] = 1
        d["jobs"]["weekly-reports"]["schedule"]["bogus"] = 1
        d["jobs"]["weekly-reports"]["holidays"]["bogus"] = 1

    problems = _job_problems(mutate)
    assert sorted(problems) == [
        "defaults.bogus: unknown key",
        "jobs.weekly-reports.holidays.bogus: unknown key",
        "jobs.weekly-reports.schedule.bogus: unknown key",
    ]
