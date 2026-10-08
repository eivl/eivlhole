import re

from typer.testing import CliRunner

from eivlhole import paths
from eivlhole.cli.app import app
from eivlhole.config import load_config

runner = CliRunner()


def add_host(ssh_server, extra=()):
    return runner.invoke(app, ["hosts", "add", "drop", "--hostname", ssh_server.host, "--user",
                               ssh_server.user, "--port", str(ssh_server.port), *extra])


def test_hosts_add_list_remove(config_dir, ssh_server):
    r = add_host(ssh_server)
    assert r.exit_code == 0, r.output
    cfg = load_config(paths.config_file())
    assert cfg.hosts["drop"].port == ssh_server.port
    assert cfg.defaults.host == "drop"  # first host becomes default
    r = runner.invoke(app, ["hosts", "list"])
    assert r.exit_code == 0 and "drop" in r.output and "127.0.0.1" in r.output
    r = add_host(ssh_server)
    assert r.exit_code == 1 and "already exists" in r.output
    r = runner.invoke(app, ["hosts", "remove", "drop"])
    assert r.exit_code == 0
    cfg = load_config(paths.config_file())
    assert cfg.hosts == {} and cfg.defaults.host is None


def test_hosts_remove_unknown(config_dir):
    r = runner.invoke(app, ["hosts", "remove", "ghost"])
    assert r.exit_code == 1 and "unknown host" in r.output


def test_hosts_trust_pins_key(config_dir, ssh_server):
    add_host(ssh_server)
    r = runner.invoke(app, ["hosts", "trust", "drop", "--yes"])
    assert r.exit_code == 0, r.output
    assert "SHA256:" in r.output
    assert f"[{ssh_server.host}]:{ssh_server.port}" in paths.known_hosts_file().read_text()
    r = runner.invoke(app, ["hosts", "trust", "drop", "--yes"])
    assert r.exit_code == 0 and "already" in r.output
    r = runner.invoke(app, ["hosts", "list"])
    assert "yes" in r.output  # trusted column


def test_hosts_trust_declined(config_dir, ssh_server):
    add_host(ssh_server)
    r = runner.invoke(app, ["hosts", "trust", "drop"], input="n\n")
    assert r.exit_code == 1 and "not trusted" in r.output
    kh = paths.known_hosts_file()
    assert not kh.exists() or kh.read_text().strip() == ""


def test_keys_init_show_force(config_dir):
    r = runner.invoke(app, ["keys", "init"])
    assert r.exit_code == 0, r.output
    assert paths.private_key_file().exists()
    r = runner.invoke(app, ["keys", "show"])
    assert r.exit_code == 0 and r.output.startswith("ssh-ed25519 ")
    r = runner.invoke(app, ["keys", "init"])
    assert r.exit_code == 1 and "already exists" in r.output
    r = runner.invoke(app, ["keys", "init", "--force"])
    assert r.exit_code == 0


def test_keys_show_before_init(config_dir):
    r = runner.invoke(app, ["keys", "show"])
    assert r.exit_code == 1 and "keys init" in r.output


def test_keys_enrol_and_test(config_dir, ssh_server):
    add_host(ssh_server)
    runner.invoke(app, ["hosts", "trust", "drop", "--yes"])
    runner.invoke(app, ["keys", "init"])
    r = runner.invoke(app, ["keys", "test", "drop"])
    assert r.exit_code == 1 and "authentication failed" in r.output
    r = runner.invoke(app, ["keys", "enrol", "drop", "--password-stdin"], input="pw\n")
    assert r.exit_code == 0, r.output
    assert "enrolled" in r.output
    assert ssh_server.authorized_keys_file().exists()
    r = runner.invoke(app, ["keys", "enrol", "drop"], input="pw\n")  # prompted
    assert r.exit_code == 0 and "already" in r.output
    r = runner.invoke(app, ["keys", "test"])  # default host
    assert r.exit_code == 0 and "OK" in r.output


def test_keys_enrol_bad_password(config_dir, ssh_server):
    add_host(ssh_server)
    runner.invoke(app, ["hosts", "trust", "drop", "--yes"])
    runner.invoke(app, ["keys", "init"])
    r = runner.invoke(app, ["keys", "enrol", "drop", "--password-stdin"], input="wrong\n")
    assert r.exit_code == 1 and "authentication failed" in r.output


def test_unattended_untrusted_host_fails_fast(config_dir, ssh_server):
    add_host(ssh_server)
    runner.invoke(app, ["keys", "init"])
    r = runner.invoke(app, ["--unattended", "keys", "test", "drop"], input="y\n")
    assert r.exit_code == 1 and "hosts trust" in r.output


def test_interactive_untrusted_host_prompts_and_pins(config_dir, ssh_server):
    add_host(ssh_server)
    runner.invoke(app, ["hosts", "trust", "drop", "--yes"])
    runner.invoke(app, ["keys", "init"])
    runner.invoke(app, ["keys", "enrol", "drop", "--password-stdin"], input="pw\n")
    paths.known_hosts_file().write_text("")  # forget the host again
    r = runner.invoke(app, ["keys", "test", "drop"], input="y\n")
    assert r.exit_code == 0, r.output
    assert "SHA256:" in r.output
    assert f"[{ssh_server.host}]:{ssh_server.port}" in paths.known_hosts_file().read_text()


def test_config_path(config_dir):
    r = runner.invoke(app, ["config", "path"])
    assert r.exit_code == 0 and str(paths.config_file()) in r.output


def test_config_dir_option(tmp_path, monkeypatch):
    monkeypatch.setenv(paths.ENV_CONFIG_DIR, "")  # recorded so the callback's change is undone
    r = runner.invoke(app, ["--config-dir", str(tmp_path / "x"), "config", "path"])
    assert r.exit_code == 0 and str(tmp_path / "x" / "config.toml") in r.output
    assert (tmp_path / "x" / "logs").is_dir()


def test_invalid_config_reports_problems(config_dir):
    paths.config_file().write_text('[hosts.bad]\nuser = "u"\n')
    r = runner.invoke(app, ["hosts", "list"])
    assert r.exit_code == 1 and "hosts.bad.hostname: required" in r.output


def test_log_file_written(config_dir):
    runner.invoke(app, ["keys", "init"])
    log = paths.log_dir() / "eivlhole.log"
    assert log.exists() and re.search(r"generated key", log.read_text())
