from pathlib import Path

import pytest

from takopi.config import (
    ConfigError,
    get_default_workspace,
    load_telegram_config,
    parse_workspaces,
)
from takopi.model import Workspace


class TestLoadTelegramConfig:
    def test_load_from_explicit_path(self, tmp_path: Path) -> None:
        config_file = tmp_path / "config.toml"
        config_file.write_text('bot_token = "test123"')

        config, path = load_telegram_config(config_file)

        assert config["bot_token"] == "test123"
        assert path == config_file

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        missing_file = tmp_path / "nonexistent.toml"

        with pytest.raises(ConfigError, match="Missing config file"):
            load_telegram_config(missing_file)

    def test_malformed_toml_raises(self, tmp_path: Path) -> None:
        bad_file = tmp_path / "bad.toml"
        bad_file.write_text("invalid = [unclosed")

        with pytest.raises(ConfigError, match="Malformed TOML"):
            load_telegram_config(bad_file)

    def test_path_exists_but_is_directory(self, tmp_path: Path) -> None:
        dir_path = tmp_path / "config_dir"
        dir_path.mkdir()

        with pytest.raises(ConfigError, match="Failed to read config file"):
            load_telegram_config(dir_path)


class TestParseWorkspaces:
    def test_empty_config(self, tmp_path: Path) -> None:
        config: dict = {}
        result = parse_workspaces(config, tmp_path / "takopi.toml")
        assert result == []

    def test_no_workspaces_section(self, tmp_path: Path) -> None:
        config = {"bot_token": "abc"}
        result = parse_workspaces(config, tmp_path / "takopi.toml")
        assert result == []

    def test_single_workspace(self, tmp_path: Path) -> None:
        workspace_dir = tmp_path / "myproject"
        workspace_dir.mkdir()
        config = {"workspaces": {"myproject": str(workspace_dir)}}

        result = parse_workspaces(config, tmp_path / "takopi.toml")

        assert len(result) == 1
        assert result[0].name == "myproject"
        assert result[0].path == workspace_dir

    def test_multiple_workspaces(self, tmp_path: Path) -> None:
        dir1 = tmp_path / "project1"
        dir2 = tmp_path / "project2"
        dir1.mkdir()
        dir2.mkdir()
        config = {
            "workspaces": {
                "project1": str(dir1),
                "project2": str(dir2),
            }
        }

        result = parse_workspaces(config, tmp_path / "takopi.toml")

        assert len(result) == 2
        names = {ws.name for ws in result}
        assert names == {"project1", "project2"}

    def test_expands_home_dir(self, tmp_path: Path) -> None:
        config = {"workspaces": {"project": "~/some/path"}}
        result = parse_workspaces(
            config, tmp_path / "takopi.toml", validate_paths=False
        )

        assert len(result) == 1
        assert "~" not in str(result[0].path)
        assert result[0].path.is_absolute()

    def test_invalid_workspaces_not_dict(self, tmp_path: Path) -> None:
        config = {"workspaces": "invalid"}

        with pytest.raises(ConfigError, match="expected a table"):
            parse_workspaces(config, tmp_path / "takopi.toml")

    def test_invalid_workspace_path_not_string(self, tmp_path: Path) -> None:
        config = {"workspaces": {"project": 123}}

        with pytest.raises(ConfigError, match="expected a non-empty string"):
            parse_workspaces(config, tmp_path / "takopi.toml")

    def test_invalid_workspace_path_empty(self, tmp_path: Path) -> None:
        config = {"workspaces": {"project": ""}}

        with pytest.raises(ConfigError, match="expected a non-empty string"):
            parse_workspaces(config, tmp_path / "takopi.toml")

    def test_nonexistent_path_with_validation(self, tmp_path: Path) -> None:
        config = {"workspaces": {"project": "/nonexistent/path"}}

        with pytest.raises(ConfigError, match="does not exist"):
            parse_workspaces(config, tmp_path / "takopi.toml", validate_paths=True)

    def test_nonexistent_path_without_validation(self, tmp_path: Path) -> None:
        config = {"workspaces": {"project": "/nonexistent/path"}}

        result = parse_workspaces(
            config, tmp_path / "takopi.toml", validate_paths=False
        )

        assert len(result) == 1
        assert result[0].name == "project"


class TestGetDefaultWorkspace:
    def test_no_default_configured(self, tmp_path: Path) -> None:
        config: dict = {}
        workspaces = [Workspace(name="project", path=tmp_path)]

        result = get_default_workspace(config, tmp_path / "takopi.toml", workspaces)

        assert result is None

    def test_valid_default(self, tmp_path: Path) -> None:
        config = {"default_workspace": "project1"}
        workspaces = [
            Workspace(name="project1", path=tmp_path / "p1"),
            Workspace(name="project2", path=tmp_path / "p2"),
        ]

        result = get_default_workspace(config, tmp_path / "takopi.toml", workspaces)

        assert result == "project1"

    def test_default_with_whitespace(self, tmp_path: Path) -> None:
        config = {"default_workspace": "  project1  "}
        workspaces = [Workspace(name="project1", path=tmp_path / "p1")]

        result = get_default_workspace(config, tmp_path / "takopi.toml", workspaces)

        assert result == "project1"

    def test_invalid_default_not_string(self, tmp_path: Path) -> None:
        config = {"default_workspace": 123}
        workspaces = [Workspace(name="project", path=tmp_path)]

        with pytest.raises(ConfigError, match="expected a non-empty string"):
            get_default_workspace(config, tmp_path / "takopi.toml", workspaces)

    def test_invalid_default_empty(self, tmp_path: Path) -> None:
        config = {"default_workspace": ""}
        workspaces = [Workspace(name="project", path=tmp_path)]

        with pytest.raises(ConfigError, match="expected a non-empty string"):
            get_default_workspace(config, tmp_path / "takopi.toml", workspaces)

    def test_unknown_default_workspace(self, tmp_path: Path) -> None:
        config = {"default_workspace": "nonexistent"}
        workspaces = [Workspace(name="project1", path=tmp_path / "p1")]

        with pytest.raises(ConfigError, match="Unknown default workspace"):
            get_default_workspace(config, tmp_path / "takopi.toml", workspaces)

    def test_unknown_default_shows_available(self, tmp_path: Path) -> None:
        config = {"default_workspace": "nonexistent"}
        workspaces = [
            Workspace(name="alpha", path=tmp_path / "a"),
            Workspace(name="beta", path=tmp_path / "b"),
        ]

        with pytest.raises(ConfigError, match="Available: alpha, beta"):
            get_default_workspace(config, tmp_path / "takopi.toml", workspaces)
