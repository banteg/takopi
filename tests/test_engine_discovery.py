from pathlib import Path
from typing import cast

import click
import pytest
import typer

from takopi import cli, engines
from takopi.config import ConfigError


def test_engine_discovery_skips_non_backend() -> None:
    ids = engines.list_backend_ids()
    assert "codex" in ids
    assert "claude" in ids
    assert "mock" not in ids


def test_cli_registers_engine_commands_sorted() -> None:
    command_names = [cmd.name for cmd in cli.app.registered_commands]
    engine_ids = engines.list_backend_ids()
    assert set(engine_ids) <= set(command_names)
    engine_commands = [name for name in command_names if name in engine_ids]
    assert engine_commands == engine_ids


def test_engine_commands_do_not_expose_engine_id_option() -> None:
    group = cast(click.Group, typer.main.get_command(cli.app))
    engine_ids = engines.list_backend_ids()

    ctx = group.make_context("takopi", [])

    for engine_id in engine_ids:
        command = group.get_command(ctx, engine_id)
        assert command is not None
        options: set[str] = set()
        for param in command.params:
            options.update(getattr(param, "opts", []))
            options.update(getattr(param, "secondary_opts", []))
        assert "--final-notify" in options
        assert "--debug" in options
        assert not any(opt.lstrip("-") == "engine-id" for opt in options)


def test_get_backend_known() -> None:
    backend = engines.get_backend("codex")
    assert backend.id == "codex"


def test_get_backend_unknown_raises() -> None:
    with pytest.raises(ConfigError, match="Unknown engine"):
        engines.get_backend("nonexistent_engine_xyz")


def test_list_backends() -> None:
    backends = engines.list_backends()
    assert len(backends) > 0
    ids = [b.id for b in backends]
    assert "codex" in ids


def test_get_engine_config_valid(tmp_path: Path) -> None:
    config = {"codex": {"profile": "default"}}
    result = engines.get_engine_config(config, "codex", tmp_path / "config.toml")
    assert result == {"profile": "default"}


def test_get_engine_config_missing(tmp_path: Path) -> None:
    config: dict = {}
    result = engines.get_engine_config(config, "codex", tmp_path / "config.toml")
    assert result == {}


def test_get_engine_config_invalid_type(tmp_path: Path) -> None:
    config = {"codex": "not a dict"}
    with pytest.raises(ConfigError, match="expected a table"):
        engines.get_engine_config(config, "codex", tmp_path / "config.toml")
