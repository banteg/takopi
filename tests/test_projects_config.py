from pathlib import Path

import pytest
from typer.testing import CliRunner

from takopi import cli
from takopi.config import ConfigError
from takopi.settings import TakopiSettings


def test_parse_projects_rejects_engine_alias() -> None:
    config = {"projects": {"codex": {"path": "/tmp/repo"}}}
    with pytest.raises(ConfigError, match="aliases must not match engine ids"):
        settings = TakopiSettings.model_validate(config)
        settings.to_projects_config(
            config_path=Path("takopi.toml"),
            engine_ids=["codex"],
            reserved=("cancel",),
        )


def test_parse_projects_default_project_must_exist() -> None:
    config = {"default_project": "z80", "projects": {}}
    with pytest.raises(ConfigError, match="default_project"):
        settings = TakopiSettings.model_validate(config)
        settings.to_projects_config(
            config_path=Path("takopi.toml"),
            engine_ids=["codex"],
            reserved=("cancel",),
        )


def test_init_writes_project(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "takopi.toml"
    monkeypatch.setattr("takopi.config.HOME_CONFIG_PATH", config_path)
    monkeypatch.setattr(cli, "resolve_default_base", lambda _: "main")

    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    monkeypatch.chdir(repo_path)

    runner = CliRunner()
    result = runner.invoke(cli.app, ["init", "z80"])
    assert result.exit_code == 0

    saved = config_path.read_text(encoding="utf-8")
    assert "[projects.z80]" in saved
    assert 'worktrees_dir = ".worktrees"' in saved
    assert 'default_engine = "codex"' in saved
    assert 'worktree_base = "main"' in saved


def test_projects_default_engine_unknown() -> None:
    config = {"projects": {"z80": {"path": "/tmp/repo", "default_engine": "nope"}}}
    settings = TakopiSettings.model_validate(config)
    with pytest.raises(ConfigError, match="projects.z80.default_engine"):
        settings.to_projects_config(
            config_path=Path("takopi.toml"),
            engine_ids=["codex"],
            reserved=("cancel",),
        )


def test_projects_relative_path_resolves(tmp_path: Path) -> None:
    config_path = tmp_path / "takopi.toml"
    settings = TakopiSettings.model_validate({"projects": {"z80": {"path": "repo"}}})
    projects = settings.to_projects_config(
        config_path=config_path,
        engine_ids=["codex"],
        reserved=("cancel",),
    )
    assert projects.projects["z80"].path == config_path.parent / "repo"
