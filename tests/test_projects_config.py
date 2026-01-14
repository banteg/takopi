from pathlib import Path

import pytest
from typer.testing import CliRunner

from takopi import cli
from takopi.config import ConfigError, read_config
from takopi.ids import RESERVED_CHAT_COMMANDS
from takopi.settings import TakopiSettings


def _base_config() -> dict:
    return {"transports": {"telegram": {"bot_token": "token", "chat_id": 123}}}


def test_parse_projects_rejects_engine_alias() -> None:
    config = {**_base_config(), "projects": {"codex": {"path": "/tmp/repo"}}}
    with pytest.raises(ConfigError, match="aliases must not match engine ids"):
        settings = TakopiSettings.model_validate(config)
        settings.to_projects_config(
            config_path=Path("takopi.toml"),
            engine_ids=["codex"],
            reserved=RESERVED_CHAT_COMMANDS,
        )


def test_parse_projects_default_project_must_exist() -> None:
    config = {**_base_config(), "default_project": "z80", "projects": {}}
    with pytest.raises(ConfigError, match="default_project"):
        settings = TakopiSettings.model_validate(config)
        settings.to_projects_config(
            config_path=Path("takopi.toml"),
            engine_ids=["codex"],
            reserved=RESERVED_CHAT_COMMANDS,
        )


def test_init_writes_project(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "takopi.toml"
    config_path.write_text(
        'transport = "telegram"\n\n[transports.telegram]\n'
        'bot_token = "token"\nchat_id = 123\n',
        encoding="utf-8",
    )
    monkeypatch.setattr("takopi.config.HOME_CONFIG_PATH", config_path)
    monkeypatch.setattr(cli, "resolve_default_base", lambda _: "main")
    monkeypatch.setattr(cli, "_load_settings_optional", lambda: (None, None))

    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    monkeypatch.chdir(repo_path)

    runner = CliRunner()
    result = runner.invoke(cli.create_app(), ["init", "z80"])
    assert result.exit_code == 0

    saved = config_path.read_text(encoding="utf-8")
    assert "[projects.z80]" in saved
    assert 'worktrees_dir = ".worktrees"' in saved
    assert 'default_engine = "codex"' in saved
    assert 'worktree_base = "main"' in saved


def test_init_migrates_legacy_config(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "takopi.toml"
    config_path.write_text('bot_token = "token"\nchat_id = 123\n', encoding="utf-8")
    monkeypatch.setattr("takopi.config.HOME_CONFIG_PATH", config_path)
    monkeypatch.setattr(cli, "resolve_default_base", lambda _: "main")
    monkeypatch.setattr(cli, "_load_settings_optional", lambda: (None, None))

    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    monkeypatch.chdir(repo_path)

    runner = CliRunner()
    result = runner.invoke(cli.create_app(), ["init", "z80"])
    assert result.exit_code == 0

    raw = read_config(config_path)
    assert "bot_token" not in raw
    assert "chat_id" not in raw
    assert raw["transport"] == "telegram"
    assert raw["transports"]["telegram"]["bot_token"] == "token"
    assert raw["transports"]["telegram"]["chat_id"] == 123
    assert "z80" in raw.get("projects", {})


def test_projects_default_engine_unknown() -> None:
    config = {
        **_base_config(),
        "projects": {"z80": {"path": "/tmp/repo", "default_engine": "nope"}},
    }
    settings = TakopiSettings.model_validate(config)
    with pytest.raises(ConfigError, match=r"projects\.z80\.default_engine"):
        settings.to_projects_config(
            config_path=Path("takopi.toml"),
            engine_ids=["codex"],
            reserved=RESERVED_CHAT_COMMANDS,
        )


def test_projects_chat_id_cannot_match_transport_chat_id() -> None:
    config = {
        "transports": {"telegram": {"bot_token": "token", "chat_id": 123}},
        "projects": {"z80": {"path": "/tmp/repo", "chat_id": 123}},
    }
    settings = TakopiSettings.model_validate(config)
    with pytest.raises(ConfigError, match="chat_id"):
        settings.to_projects_config(
            config_path=Path("takopi.toml"),
            engine_ids=["codex"],
            reserved=RESERVED_CHAT_COMMANDS,
        )


def test_projects_chat_id_must_be_unique() -> None:
    config = {
        "transports": {"telegram": {"bot_token": "token", "chat_id": 123}},
        "projects": {
            "a": {"path": "/tmp/a", "chat_id": -10},
            "b": {"path": "/tmp/b", "chat_id": -10},
        },
    }
    settings = TakopiSettings.model_validate(config)
    with pytest.raises(ConfigError, match="chat_id"):
        settings.to_projects_config(
            config_path=Path("takopi.toml"),
            engine_ids=["codex"],
            reserved=RESERVED_CHAT_COMMANDS,
        )


def test_projects_relative_path_resolves(tmp_path: Path) -> None:
    config_path = tmp_path / "takopi.toml"
    settings = TakopiSettings.model_validate(
        {**_base_config(), "projects": {"z80": {"path": "repo"}}}
    )
    projects = settings.to_projects_config(
        config_path=config_path,
        engine_ids=["codex"],
        reserved=RESERVED_CHAT_COMMANDS,
    )
    assert projects.projects["z80"].path == config_path.parent / "repo"
