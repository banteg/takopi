from pathlib import Path
import tempfile

import pytest

from takopi.config import (
    ConfigError,
    _read_config,
    load_telegram_config,
    HOME_CONFIG_PATH,
)


def test_read_config_missing_file() -> None:
    with pytest.raises(ConfigError) as exc_info:
        _read_config(Path("/nonexistent/file.toml"))

    assert "Missing config file" in str(exc_info.value)


def test_read_config_malformed_toml() -> None:
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".toml", delete=False
    ) as f:
        f.write("invalid [toml")
        f.flush()
        cfg_path = Path(f.name)

    try:
        with pytest.raises(ConfigError) as exc_info:
            _read_config(cfg_path)

        assert "Malformed TOML" in str(exc_info.value)
    finally:
        cfg_path.unlink()


def test_read_config_valid() -> None:
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".toml", delete=False
    ) as f:
        f.write('[test]\nkey = "value"')
        f.flush()
        cfg_path = Path(f.name)

    try:
        result = _read_config(cfg_path)
        assert result == {"test": {"key": "value"}}
    finally:
        cfg_path.unlink()


def test_read_config_os_error() -> None:
    # Create a directory instead of a file
    with tempfile.TemporaryDirectory() as tmpdir:
        dir_path = Path(tmpdir) / "test.toml"
        dir_path.mkdir()

        with pytest.raises(ConfigError) as exc_info:
            _read_config(dir_path)

        assert "Failed to read config file" in str(exc_info.value)


def test_load_telegram_config_with_path() -> None:
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".toml", delete=False
    ) as f:
        f.write('[telegram]\nbot_token = "test"')
        f.flush()
        cfg_path = Path(f.name)

    try:
        config, path = load_telegram_config(cfg_path)
        assert path == cfg_path
        assert config == {"telegram": {"bot_token": "test"}}
    finally:
        cfg_path.unlink()


def test_load_telegram_config_with_tilde() -> None:
    # This test uses the home directory, so we'll create a temp file in HOME
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".toml", delete=False
    ) as f:
        f.write('[telegram]\nbot_token = "test"')
        f.flush()
        cfg_path = Path(f.name)

    try:
        # Use tilde expansion
        expanded = cfg_path.expanduser()
        config, path = load_telegram_config(expanded)
        assert path == cfg_path
        assert config == {"telegram": {"bot_token": "test"}}
    finally:
        cfg_path.unlink()


def test_load_telegram_config_path_is_directory() -> None:
    # Test when path is a directory
    with tempfile.TemporaryDirectory() as tmpdir:
        cfg_dir = Path(tmpdir) / "takopi.toml"
        cfg_dir.mkdir()

        with pytest.raises(ConfigError) as exc_info:
            load_telegram_config(cfg_dir)

        assert "Failed to read config file" in str(exc_info.value)
        assert "Is a directory" in str(exc_info.value)


def test_home_config_path() -> None:
    # Just verify the constant is set correctly
    assert HOME_CONFIG_PATH.name == "takopi.toml"
    assert HOME_CONFIG_PATH.parent.name == ".takopi"
