from __future__ import annotations

import pytest

from takopi import cli
from takopi.config import ConfigError
from takopi.lockfile import LockError
from takopi.settings import TakopiSettings


def _settings(overrides: dict | None = None) -> TakopiSettings:
    payload = {
        "transport": "telegram",
        "transports": {"telegram": {"bot_token": "token", "chat_id": 123}},
    }
    if overrides:
        payload.update(overrides)
    return TakopiSettings.model_validate(payload)


def test_parse_key_path_valid() -> None:
    assert cli._parse_key_path("transports.telegram.chat_id") == [
        "transports",
        "telegram",
        "chat_id",
    ]


def test_parse_key_path_invalid_segment() -> None:
    with pytest.raises(ConfigError):
        cli._parse_key_path("transports..chat_id")


def test_parse_value_toml_and_fallback() -> None:
    assert cli._parse_value("true") is True
    assert cli._parse_value("123") == 123
    assert cli._parse_value("not-toml") == "not-toml"


def test_toml_literal_and_error() -> None:
    assert cli._toml_literal("hello") == '"hello"'
    with pytest.raises(ConfigError):
        cli._toml_literal({"a": 1})


def test_flatten_config() -> None:
    flattened = cli._flatten_config(
        {"transports": {"telegram": {"chat_id": 123}}, "watch_config": True}
    )
    assert ("transports.telegram.chat_id", 123) in flattened
    assert ("watch_config", True) in flattened


def test_normalized_value_from_settings() -> None:
    settings = _settings()
    assert cli._normalized_value_from_settings(settings, ["transport"]) == "telegram"
    assert (
        cli._normalized_value_from_settings(
            settings, ["transports", "telegram", "chat_id"]
        )
        == 123
    )


def test_should_run_interactive(monkeypatch) -> None:
    class _Tty:
        def isatty(self) -> bool:
            return True

    class _NotTty:
        def isatty(self) -> bool:
            return False

    monkeypatch.setenv("TAKOPI_NO_INTERACTIVE", "1")
    assert cli._should_run_interactive() is False
    monkeypatch.delenv("TAKOPI_NO_INTERACTIVE")

    monkeypatch.setattr(cli.sys, "stdin", _Tty())
    monkeypatch.setattr(cli.sys, "stdout", _Tty())
    assert cli._should_run_interactive() is True

    monkeypatch.setattr(cli.sys, "stdin", _NotTty())
    monkeypatch.setattr(cli.sys, "stdout", _Tty())
    assert cli._should_run_interactive() is False


def test_resolve_transport_id_override(monkeypatch) -> None:
    assert cli._resolve_transport_id("  telegram ") == "telegram"
    with pytest.raises(ConfigError):
        cli._resolve_transport_id("   ")

    def _raise() -> None:
        raise ConfigError("boom")

    monkeypatch.setattr(cli, "load_or_init_config", _raise)
    assert cli._resolve_transport_id(None) == "telegram"


def test_doctor_file_checks() -> None:
    settings = _settings()
    checks = cli._doctor_file_checks(settings)
    assert checks[0].detail == "disabled"

    settings = _settings(
        {
            "transports": {
                "telegram": {
                    "bot_token": "token",
                    "chat_id": 1,
                    "files": {"enabled": True},
                }
            }
        }
    )
    checks = cli._doctor_file_checks(settings)
    assert checks[0].status == "warning"


def test_doctor_voice_checks(monkeypatch) -> None:
    settings = _settings()
    checks = cli._doctor_voice_checks(settings)
    assert checks[0].detail == "disabled"

    settings = _settings(
        {
            "transports": {
                "telegram": {
                    "bot_token": "token",
                    "chat_id": 1,
                    "voice_transcription": True,
                }
            }
        }
    )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    checks = cli._doctor_voice_checks(settings)
    assert checks[0].status == "error"
    assert checks[0].detail == "API key not set"

    settings_with_key = _settings(
        {
            "transports": {
                "telegram": {
                    "bot_token": "token",
                    "chat_id": 1,
                    "voice_transcription": True,
                    "voice_transcription_api_key": "local",
                }
            }
        }
    )
    checks = cli._doctor_voice_checks(settings_with_key)
    assert checks[0].status == "ok"
    assert checks[0].detail == "voice_transcription_api_key set"

    monkeypatch.setenv("OPENAI_API_KEY", "key")
    checks = cli._doctor_voice_checks(settings)
    assert checks[0].status == "ok"


def test_load_settings_optional(monkeypatch, tmp_path) -> None:
    def _raise() -> None:
        raise ConfigError("boom")

    monkeypatch.setattr(cli, "load_settings_if_exists", _raise)
    assert cli._load_settings_optional() == (None, None)

    monkeypatch.setattr(cli, "load_settings_if_exists", lambda: None)
    assert cli._load_settings_optional() == (None, None)

    settings = _settings()
    config_path = tmp_path / "takopi.toml"
    monkeypatch.setattr(cli, "load_settings_if_exists", lambda: (settings, config_path))
    assert cli._load_settings_optional() == (settings, config_path)


def test_acquire_config_lock_reports_error(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "takopi.toml"
    error = LockError(path=config_path, state="running")

    def _raise(*_args, **_kwargs):
        raise error

    messages: list[tuple[str, bool]] = []
    monkeypatch.setattr(cli, "acquire_lock", _raise)
    monkeypatch.setattr(
        cli.typer, "echo", lambda msg, err=False: messages.append((msg, err))
    )

    with pytest.raises(cli.typer.Exit) as exc:
        cli.acquire_config_lock(config_path, "token")

    assert exc.value.exit_code == 1
    assert any("already running" in msg for msg, _ in messages)
