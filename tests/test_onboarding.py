from __future__ import annotations

from pathlib import Path

from takopi import engines, onboarding


def test_check_setup_marks_missing_codex(monkeypatch, tmp_path: Path) -> None:
    backend = engines.get_backend("codex")
    monkeypatch.setattr(onboarding.shutil, "which", lambda _name: None)
    monkeypatch.setattr(
        onboarding,
        "load_telegram_config",
        lambda: ({"bot_token": "token", "chat_id": 123}, tmp_path / "takopi.toml"),
    )

    result = onboarding.check_setup(backend)

    titles = {issue.title for issue in result.issues}
    assert "install codex" in titles
    assert "create a config" not in titles
    assert result.ok is False


def test_check_setup_marks_missing_config(monkeypatch) -> None:
    backend = engines.get_backend("codex")
    monkeypatch.setattr(onboarding.shutil, "which", lambda _name: "/usr/bin/codex")

    def _raise() -> None:
        raise onboarding.ConfigError("Missing config file")

    monkeypatch.setattr(onboarding, "load_telegram_config", _raise)

    result = onboarding.check_setup(backend)

    titles = {issue.title for issue in result.issues}
    assert "create a config" in titles
    assert result.config_path == onboarding.HOME_CONFIG_PATH


def test_check_setup_marks_invalid_chat_id(monkeypatch, tmp_path: Path) -> None:
    backend = engines.get_backend("codex")
    monkeypatch.setattr(onboarding.shutil, "which", lambda _name: "/usr/bin/codex")
    monkeypatch.setattr(
        onboarding,
        "load_telegram_config",
        lambda: ({"bot_token": "token", "chat_id": "123"}, tmp_path / "takopi.toml"),
    )

    result = onboarding.check_setup(backend)

    titles = {issue.title for issue in result.issues}
    assert "create a config" in titles


def test_onboarding_prefers_new_config_path_for_legacy_file(monkeypatch) -> None:
    backend = engines.get_backend("codex")
    monkeypatch.setattr(onboarding.shutil, "which", lambda _name: "/usr/bin/codex")
    legacy_path = Path.home() / ".codex" / "takopi.toml"
    monkeypatch.setattr(
        onboarding,
        "load_telegram_config",
        lambda: ({"bot_token": "token", "chat_id": "123"}, legacy_path),
    )

    result = onboarding.check_setup(backend)

    issue = next(issue for issue in result.issues if issue.title == "create a config")
    assert any("~/.takopi/takopi.toml" in line for line in issue.lines)
    assert all(".codex/takopi.toml" not in line for line in issue.lines)
