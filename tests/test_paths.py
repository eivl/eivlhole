from pathlib import Path

import platformdirs

from eivlhole import paths


def test_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv(paths.ENV_CONFIG_DIR, str(tmp_path / "cfg"))
    assert paths.config_dir() == tmp_path / "cfg"
    assert paths.config_file() == tmp_path / "cfg" / "config.toml"
    assert paths.private_key_file() == tmp_path / "cfg" / "keys" / "id_ed25519"
    assert paths.public_key_file() == tmp_path / "cfg" / "keys" / "id_ed25519.pub"
    assert paths.known_hosts_file() == tmp_path / "cfg" / "known_hosts"
    assert paths.history_file() == tmp_path / "cfg" / "history.sqlite"
    assert paths.log_dir() == tmp_path / "cfg" / "logs"


def test_default_uses_platformdirs(monkeypatch):
    monkeypatch.delenv(paths.ENV_CONFIG_DIR, raising=False)
    expected = Path(platformdirs.user_config_dir(paths.APP_NAME, appauthor=False, roaming=True))
    assert paths.config_dir() == expected


def test_ensure_dirs_creates_tree(monkeypatch, tmp_path):
    monkeypatch.setenv(paths.ENV_CONFIG_DIR, str(tmp_path / "cfg"))
    root = paths.ensure_dirs()
    assert root == tmp_path / "cfg"
    assert paths.keys_dir().is_dir()
    assert paths.log_dir().is_dir()
