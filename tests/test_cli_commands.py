from __future__ import annotations

from pathlib import Path
import tomllib

from typer.testing import CliRunner

from takopi import cli
from takopi.config import ConfigError
from takopi.plugins import (
    COMMAND_GROUP,
    ENGINE_GROUP,
    TRANSPORT_GROUP,
    PluginLoadError,
)
from takopi.settings import TakopiSettings
from tests.plugin_fixtures import FakeEntryPoint


def _min_config() -> dict:
    return {
        "transport": "telegram",
        "transports": {"telegram": {"bot_token": "token", "chat_id": 123}},
    }


def test_init_registers_project(monkeypatch, tmp_path: Path) -> None:
    config = _min_config()
    config_path = tmp_path / "takopi.toml"
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    monkeypatch.chdir(repo_path)

    monkeypatch.setattr(cli, "load_or_init_config", lambda: (config, config_path))
    monkeypatch.setattr(cli, "resolve_main_worktree_root", lambda _path: None)
    monkeypatch.setattr(cli, "resolve_default_base", lambda _path: "main")
    monkeypatch.setattr(cli, "list_backend_ids", lambda allowlist=None: ["codex"])
    monkeypatch.setattr(cli, "resolve_plugins_allowlist", lambda _settings: None)
    monkeypatch.setattr(cli.typer, "prompt", lambda *args, **kwargs: "demo")

    runner = CliRunner()
    result = runner.invoke(cli.create_app(), ["init", "--default"])

    assert result.exit_code == 0
    assert "saved project" in result.output

    data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    project = data["projects"]["demo"]
    assert project["path"] == str(repo_path)
    assert project["default_engine"] == "codex"
    assert project["worktrees_dir"] == ".worktrees"
    assert project["worktree_base"] == "main"
    assert data["default_project"] == "demo"


def test_init_declines_overwrite(monkeypatch, tmp_path: Path) -> None:
    config = _min_config()
    config["projects"] = {"demo": {"path": "/tmp/repo"}}
    config_path = tmp_path / "takopi.toml"
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    monkeypatch.chdir(repo_path)

    monkeypatch.setattr(cli, "load_or_init_config", lambda: (config, config_path))
    monkeypatch.setattr(cli, "resolve_main_worktree_root", lambda _path: None)
    monkeypatch.setattr(cli, "resolve_default_base", lambda _path: None)
    monkeypatch.setattr(cli, "list_backend_ids", lambda allowlist=None: ["codex"])
    monkeypatch.setattr(cli, "resolve_plugins_allowlist", lambda _settings: None)
    monkeypatch.setattr(cli.typer, "confirm", lambda *args, **kwargs: False)

    runner = CliRunner()
    result = runner.invoke(cli.create_app(), ["init", "demo"])

    assert result.exit_code == 1


def test_plugins_cmd_loads_and_reports_errors(monkeypatch) -> None:
    entrypoints = {
        ENGINE_GROUP: [
            FakeEntryPoint(
                "codex",
                "takopi.runners.codex:BACKEND",
                ENGINE_GROUP,
                dist_name="takopi",
            ),
            FakeEntryPoint(
                "broken",
                "takopi.runners.broken:BACKEND",
                ENGINE_GROUP,
                dist_name="takopi",
            ),
        ],
        TRANSPORT_GROUP: [
            FakeEntryPoint(
                "telegram",
                "takopi.transports.telegram:BACKEND",
                TRANSPORT_GROUP,
                dist_name="takopi",
            )
        ],
        COMMAND_GROUP: [
            FakeEntryPoint(
                "hello",
                "takopi.commands.hello:BACKEND",
                COMMAND_GROUP,
                dist_name="thirdparty",
            )
        ],
    }

    def _list_entrypoints(group: str, reserved_ids=None):
        _ = reserved_ids
        return entrypoints[group]

    calls: list[tuple[str, str]] = []

    def _get_backend(name: str, allowlist=None):
        _ = allowlist
        calls.append(("engine", name))
        if name == "broken":
            raise ConfigError("boom")
        return object()

    def _get_transport(name: str, allowlist=None):
        _ = allowlist
        calls.append(("transport", name))
        return object()

    def _get_command(name: str, allowlist=None):
        _ = allowlist
        calls.append(("command", name))
        return object()

    monkeypatch.setattr(cli, "_load_settings_optional", lambda: (None, None))
    monkeypatch.setattr(cli, "resolve_plugins_allowlist", lambda _settings: ["takopi"])
    monkeypatch.setattr(cli, "list_entrypoints", _list_entrypoints)
    monkeypatch.setattr(cli, "get_backend", _get_backend)
    monkeypatch.setattr(cli, "get_transport", _get_transport)
    monkeypatch.setattr(cli, "get_command", _get_command)
    monkeypatch.setattr(
        cli,
        "get_load_errors",
        lambda: [
            PluginLoadError(
                ENGINE_GROUP,
                "broken",
                "takopi.runners.broken:BACKEND",
                "takopi",
                "boom",
            ),
            PluginLoadError(
                TRANSPORT_GROUP,
                "wire",
                "takopi.transports.wire:BACKEND",
                "takopi",
                "missing",
            ),
            PluginLoadError(
                COMMAND_GROUP,
                "hello",
                "takopi.commands.hello:BACKEND",
                "thirdparty",
                "oops",
            ),
        ],
    )

    runner = CliRunner()
    result = runner.invoke(cli.create_app(), ["plugins", "--load"])

    assert result.exit_code == 0
    assert "engine backends:" in result.output
    assert "transport backends:" in result.output
    assert "command backends:" in result.output
    assert "codex (takopi) enabled" in result.output
    assert "hello (thirdparty) disabled" in result.output
    assert "errors:" in result.output
    assert "engine broken (takopi): boom" in result.output
    assert "transport wire (takopi): missing" in result.output
    assert "command hello (thirdparty): oops" in result.output

    assert ("engine", "codex") in calls
    assert ("engine", "broken") in calls
    assert ("transport", "telegram") in calls
    assert ("command", "hello") not in calls


def test_onboarding_paths_calls_debug(monkeypatch) -> None:
    called = {"count": 0}

    def _debug() -> None:
        called["count"] += 1

    monkeypatch.setattr(cli.onboarding, "debug_onboarding_paths", _debug)

    runner = CliRunner()
    result = runner.invoke(cli.create_app(), ["onboarding-paths"])

    assert result.exit_code == 0
    assert called["count"] == 1


def test_config_path_cmd_outputs_override(tmp_path: Path) -> None:
    config_path = tmp_path / "takopi.toml"
    runner = CliRunner()
    result = runner.invoke(
        cli.create_app(),
        ["config", "path", "--config-path", str(config_path)],
    )

    assert result.exit_code == 0
    assert result.output.strip() == str(config_path)


def test_config_path_cmd_defaults_to_home(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    config_path = tmp_path / ".takopi" / "takopi.toml"
    monkeypatch.setattr(cli, "HOME_CONFIG_PATH", config_path)

    runner = CliRunner()
    result = runner.invoke(cli.create_app(), ["config", "path"])

    assert result.exit_code == 0
    assert result.output.strip() == "~/.takopi/takopi.toml"


def test_doctor_rejects_non_telegram_transport(monkeypatch) -> None:
    settings = TakopiSettings.model_validate(
        {
            "transport": "local",
            "transports": {"telegram": {"bot_token": "token", "chat_id": 123}},
        }
    )
    monkeypatch.setattr(cli, "load_settings", lambda: (settings, Path("x")))

    runner = CliRunner()
    result = runner.invoke(cli.create_app(), ["doctor"])

    assert result.exit_code == 1
    assert "telegram transport only" in result.output
