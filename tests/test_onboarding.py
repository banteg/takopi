from __future__ import annotations

from pathlib import Path

from takopi import engines
from takopi.settings import TakopiSettings
from takopi.telegram import onboarding


def test_check_setup_marks_missing_codex(monkeypatch, tmp_path: Path) -> None:
    backend = engines.get_backend("codex")
    monkeypatch.setattr(onboarding.shutil, "which", lambda _name: None)
    monkeypatch.setattr(
        onboarding,
        "load_settings",
        lambda: (
            TakopiSettings.model_validate(
                {
                    "transport": "telegram",
                    "transports": {"telegram": {"bot_token": "token", "chat_id": 123}},
                }
            ),
            tmp_path / "takopi.toml",
        ),
    )

    result = onboarding.check_setup(backend)

    titles = {issue.title for issue in result.issues}
    assert "install codex" in titles
    assert "create a config" not in titles
    assert result.ok is False


def test_check_setup_marks_missing_config(monkeypatch, tmp_path: Path) -> None:
    backend = engines.get_backend("codex")
    config_path = tmp_path / "takopi.toml"
    monkeypatch.setattr(onboarding.shutil, "which", lambda _name: "/usr/bin/codex")
    monkeypatch.setattr(onboarding, "resolve_config_path", lambda: config_path)

    def _raise() -> None:
        raise onboarding.ConfigError("Missing config file")

    monkeypatch.setattr(onboarding, "load_settings", _raise)

    result = onboarding.check_setup(backend)

    titles = {issue.title for issue in result.issues}
    assert "create a config" in titles
    assert result.config_path == config_path


def test_check_setup_marks_invalid_bot_token(monkeypatch, tmp_path: Path) -> None:
    backend = engines.get_backend("codex")
    monkeypatch.setattr(onboarding.shutil, "which", lambda _name: "/usr/bin/codex")

    def _fail_require(*_args, **_kwargs):
        raise onboarding.ConfigError("Missing bot token")

    monkeypatch.setattr(
        onboarding,
        "load_settings",
        lambda: (
            TakopiSettings.model_validate(
                {
                    "transport": "telegram",
                    "transports": {"telegram": {"bot_token": "token", "chat_id": 123}},
                }
            ),
            tmp_path / "takopi.toml",
        ),
    )
    monkeypatch.setattr(onboarding, "require_telegram", _fail_require)

    result = onboarding.check_setup(backend)

    titles = {issue.title for issue in result.issues}
    assert "configure telegram" in titles


def test_check_setup_skips_telegram_validation_for_external_transport(
    monkeypatch, tmp_path: Path
) -> None:
    backend = engines.get_backend("codex")
    monkeypatch.setattr(onboarding.shutil, "which", lambda _name: "/usr/bin/codex")
    monkeypatch.setattr(
        onboarding,
        "load_settings",
        lambda: (
            TakopiSettings.model_validate(
                {"transport": "my-transport", "transports": {}}
            ),
            tmp_path / "takopi.toml",
        ),
    )

    result = onboarding.check_setup(backend, transport_override="my-transport")

    assert result.ok is True
    assert len(result.issues) == 0


def test_check_setup_external_transport_missing_config(
    monkeypatch, tmp_path: Path
) -> None:
    backend = engines.get_backend("codex")
    config_path = tmp_path / "takopi.toml"
    monkeypatch.setattr(onboarding.shutil, "which", lambda _name: "/usr/bin/codex")
    monkeypatch.setattr(onboarding, "resolve_config_path", lambda: config_path)

    def _raise() -> None:
        raise onboarding.ConfigError("Missing config file")

    monkeypatch.setattr(onboarding, "load_settings", _raise)

    result = onboarding.check_setup(backend, transport_override="my-transport")

    titles = {issue.title for issue in result.issues}
    assert "create a config" in titles
    assert "configure telegram" not in titles
