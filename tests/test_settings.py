from __future__ import annotations

from pathlib import Path

import pytest

from takopi.config import ConfigError, read_config
from takopi.settings import (
    TakopiSettings,
    load_settings,
    load_settings_if_exists,
    require_telegram,
    validate_settings_data,
)


def test_load_settings_from_toml(tmp_path: Path) -> None:
    config_path = tmp_path / "takopi.toml"
    config_path.write_text(
        'transport = "telegram"\n\n'
        "[transports.telegram]\n"
        'bot_token = "token"\n'
        "chat_id = 123\n\n"
        "[codex]\n"
        'model = "gpt-4"\n',
        encoding="utf-8",
    )

    settings, loaded_path = load_settings(config_path)

    assert loaded_path == config_path
    assert settings.transport == "telegram"
    assert settings.transports.telegram.chat_id == 123
    assert settings.engine_config("codex", config_path=config_path)["model"] == "gpt-4"

    token, chat_id = require_telegram(settings, config_path)
    assert token == "token"
    assert chat_id == 123

    assert settings.transports.telegram.bot_token == "token"


def test_env_overrides_toml(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "takopi.toml"
    config_path.write_text(
        'default_engine = "codex"\n'
        'transport = "telegram"\n\n'
        "[transports.telegram]\n"
        'bot_token = "token"\n'
        "chat_id = 123\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("TAKOPI__DEFAULT_ENGINE", "claude")

    settings, _ = load_settings(config_path)

    assert settings.default_engine == "claude"


def test_legacy_keys_migrated(tmp_path: Path) -> None:
    config_path = tmp_path / "takopi.toml"
    config_path.write_text('bot_token = "token"\nchat_id = 123\n', encoding="utf-8")

    settings, loaded_path = load_settings(config_path)

    assert loaded_path == config_path
    assert settings.transports.telegram.chat_id == 123
    raw = read_config(config_path)
    assert "bot_token" not in raw
    assert "chat_id" not in raw
    assert raw["transports"]["telegram"]["bot_token"] == "token"
    assert raw["transports"]["telegram"]["chat_id"] == 123
    assert raw["transport"] == "telegram"


def test_validate_settings_data_rejects_invalid_bot_token_type(tmp_path: Path) -> None:
    config_path = tmp_path / "takopi.toml"
    data = {
        "transport": "telegram",
        "transports": {"telegram": {"bot_token": 123, "chat_id": 123}},
    }

    with pytest.raises(ConfigError, match="bot_token"):
        validate_settings_data(data, config_path=config_path)


def test_validate_settings_data_rejects_empty_default_engine(tmp_path: Path) -> None:
    config_path = tmp_path / "takopi.toml"
    data = {
        "default_engine": "   ",
        "transport": "telegram",
        "transports": {"telegram": {"bot_token": "token", "chat_id": 123}},
    }

    with pytest.raises(ConfigError, match="default_engine"):
        validate_settings_data(data, config_path=config_path)


def test_validate_settings_data_rejects_empty_default_project(tmp_path: Path) -> None:
    config_path = tmp_path / "takopi.toml"
    data = {
        "default_project": "   ",
        "transports": {"telegram": {"bot_token": "token", "chat_id": 123}},
    }

    with pytest.raises(ConfigError, match="default_project"):
        validate_settings_data(data, config_path=config_path)


def test_validate_settings_data_rejects_empty_project_path(tmp_path: Path) -> None:
    config_path = tmp_path / "takopi.toml"
    data = {
        "projects": {"z80": {"path": "   "}},
        "transports": {"telegram": {"bot_token": "token", "chat_id": 123}},
    }

    with pytest.raises(ConfigError, match="path"):
        validate_settings_data(data, config_path=config_path)


def test_engine_config_none_and_invalid(tmp_path: Path) -> None:
    config_path = tmp_path / "takopi.toml"
    settings = TakopiSettings.model_validate(
        {
            "transport": "telegram",
            "transports": {"telegram": {"bot_token": "token", "chat_id": 123}},
            "codex": None,
        }
    )
    assert settings.engine_config("codex", config_path=config_path) == {}

    settings = TakopiSettings.model_validate(
        {
            "transport": "telegram",
            "transports": {"telegram": {"bot_token": "token", "chat_id": 123}},
            "codex": "nope",
        }
    )
    with pytest.raises(ConfigError, match="codex"):
        settings.engine_config("codex", config_path=config_path)


def test_transport_config_telegram_and_extra(tmp_path: Path) -> None:
    config_path = tmp_path / "takopi.toml"
    settings = TakopiSettings.model_validate(
        {
            "transport": "telegram",
            "transports": {"telegram": {"bot_token": "token", "chat_id": 123}},
        }
    )
    telegram = settings.transport_config("telegram", config_path=config_path)
    assert telegram["bot_token"] == "token"
    assert telegram["chat_id"] == 123

    settings = TakopiSettings.model_validate(
        {
            "transport": "telegram",
            "transports": {
                "telegram": {"bot_token": "token", "chat_id": 123},
                "discord": None,
            },
        }
    )
    assert settings.transport_config("discord", config_path=config_path) == {}

    settings = TakopiSettings.model_validate(
        {
            "transport": "telegram",
            "transports": {
                "telegram": {"bot_token": "token", "chat_id": 123},
                "discord": "nope",
            },
        }
    )
    with pytest.raises(ConfigError, match=r"transports\.discord"):
        settings.transport_config("discord", config_path=config_path)


def test_bot_token_none_rejected(tmp_path: Path) -> None:
    config_path = tmp_path / "takopi.toml"
    data = {
        "transport": "telegram",
        "transports": {"telegram": {"bot_token": None, "chat_id": 123}},
    }
    with pytest.raises(ConfigError, match="bot_token"):
        validate_settings_data(data, config_path=config_path)


def test_require_telegram_rejects_non_telegram_transport(tmp_path: Path) -> None:
    config_path = tmp_path / "takopi.toml"
    settings = TakopiSettings.model_validate(
        {
            "transport": "discord",
            "transports": {"telegram": {"bot_token": "token", "chat_id": 123}},
        }
    )
    with pytest.raises(ConfigError, match="Unsupported transport"):
        require_telegram(settings, config_path)


def test_load_settings_if_exists_missing(tmp_path: Path) -> None:
    config_path = tmp_path / "missing.toml"
    assert load_settings_if_exists(config_path) is None


def test_load_settings_missing_file(tmp_path: Path) -> None:
    config_path = tmp_path / "missing.toml"
    with pytest.raises(ConfigError, match="Missing config file"):
        load_settings(config_path)


def test_load_settings_if_exists_loads(tmp_path: Path) -> None:
    config_path = tmp_path / "takopi.toml"
    config_path.write_text(
        'transport = "telegram"\n\n[transports.telegram]\n'
        'bot_token = "token"\nchat_id = 123\n',
        encoding="utf-8",
    )

    loaded = load_settings_if_exists(config_path)
    assert loaded is not None
    settings, loaded_path = loaded
    assert loaded_path == config_path


def test_load_settings_if_exists_rejects_non_file(tmp_path: Path) -> None:
    config_path = tmp_path / "config_dir"
    config_path.mkdir()
    with pytest.raises(ConfigError, match="exists but is not a file"):
        load_settings_if_exists(config_path)


def test_load_settings_rejects_non_file(tmp_path: Path) -> None:
    config_path = tmp_path / "config_dir"
    config_path.mkdir()
    with pytest.raises(ConfigError, match="exists but is not a file"):
        load_settings(config_path)
