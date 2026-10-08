"""Locations of per-user config and state."""

from __future__ import annotations

import os
from pathlib import Path

import platformdirs

APP_NAME = "eivlhole"
ENV_CONFIG_DIR = "EIVLHOLE_CONFIG_DIR"


def config_dir() -> Path:
    override = os.environ.get(ENV_CONFIG_DIR)
    if override:
        return Path(override).expanduser()
    return Path(platformdirs.user_config_dir(APP_NAME, appauthor=False, roaming=True))


def config_file() -> Path:
    return config_dir() / "config.toml"


def keys_dir() -> Path:
    return config_dir() / "keys"


def private_key_file() -> Path:
    return keys_dir() / "id_ed25519"


def public_key_file() -> Path:
    return keys_dir() / "id_ed25519.pub"


def known_hosts_file() -> Path:
    return config_dir() / "known_hosts"


def history_file() -> Path:
    return config_dir() / "history.sqlite"


def log_dir() -> Path:
    return config_dir() / "logs"


def ensure_dirs() -> Path:
    root = config_dir()
    for d in (root, keys_dir(), log_dir()):
        d.mkdir(parents=True, exist_ok=True)
    return root
