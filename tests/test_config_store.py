from __future__ import annotations

from pathlib import Path

import pytest

from takopi.config import ConfigError, read_config, write_config


def test_read_write_config_round_trip(tmp_path: Path) -> None:
    config_path = tmp_path / "takopi.toml"
    payload = {
        "default_engine": "codex",
        "projects": {"z80": {"path": "/tmp/repo"}},
    }

    write_config(payload, config_path)
    loaded = read_config(config_path)

    assert loaded == payload


def test_read_config_missing_file(tmp_path: Path) -> None:
    config_path = tmp_path / "missing.toml"
    with pytest.raises(ConfigError, match="Missing config file"):
        read_config(config_path)


def test_read_config_invalid_toml(tmp_path: Path) -> None:
    config_path = tmp_path / "takopi.toml"
    config_path.write_text("nope = [", encoding="utf-8")
    with pytest.raises(ConfigError, match="Malformed TOML"):
        read_config(config_path)


def test_read_config_non_file(tmp_path: Path) -> None:
    config_path = tmp_path / "config_dir"
    config_path.mkdir()
    with pytest.raises(ConfigError, match="exists but is not a file"):
        read_config(config_path)
