from __future__ import annotations

import sys
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any, cast

import anyio
import typer

from ..config import ConfigError, load_or_init_config, write_config
from ..config_migrations import migrate_config
from ..logging import setup_logging
from ..settings import TakopiSettings
from ..telegram import onboarding
from .init import _ensure_projects_table
from .run import _load_settings_optional


def chat_id(
    token: str | None = typer.Option(
        None,
        "--token",
        help="Telegram bot token (defaults to config if available).",
    ),
    project: str | None = typer.Option(
        None,
        "--project",
        help="Project alias to print a chat_id snippet for.",
    ),
) -> None:
    """Capture a Telegram chat id and exit."""
    setup_logging_fn = cast(
        Callable[..., None],
        _resolve_cli_attr("setup_logging") or setup_logging,
    )
    load_settings_optional_fn = cast(
        Callable[[], tuple[TakopiSettings | None, Path | None]],
        _resolve_cli_attr("_load_settings_optional") or _load_settings_optional,
    )
    onboarding_mod = cast(
        Any,
        _resolve_cli_attr("onboarding") or onboarding,
    )
    load_or_init_config_fn = cast(
        Callable[[], tuple[dict, Path]],
        _resolve_cli_attr("load_or_init_config") or load_or_init_config,
    )
    ensure_projects_table_fn = cast(
        Callable[[dict, Path], dict],
        _resolve_cli_attr("_ensure_projects_table") or _ensure_projects_table,
    )
    migrate_config_fn = cast(
        Callable[..., object],
        _resolve_cli_attr("migrate_config") or migrate_config,
    )
    write_config_fn = cast(
        Callable[[dict, Path], None],
        _resolve_cli_attr("write_config") or write_config,
    )

    setup_logging_fn(debug=False, cache_logger_on_first_use=False)
    if token is None:
        settings, _ = load_settings_optional_fn()
        if settings is not None:
            tg = settings.transports.telegram
            token = tg.bot_token or None
    chat = anyio.run(partial(onboarding_mod.capture_chat_id, token=token))
    if chat is None:
        raise typer.Exit(code=1)
    if project:
        project = project.strip()
        if not project:
            raise ConfigError("Invalid `--project`; expected a non-empty string.")

        config, config_path = load_or_init_config_fn()
        if config_path.exists():
            applied = migrate_config_fn(config, config_path=config_path)
            if applied:
                write_config_fn(config, config_path)

        projects = ensure_projects_table_fn(config, config_path)
        entry = projects.get(project)
        if entry is None:
            lowered = project.lower()
            for key, value in projects.items():
                if isinstance(key, str) and key.lower() == lowered:
                    entry = value
                    project = key
                    break
        if entry is None:
            raise ConfigError(
                f"Unknown project {project!r}; run `takopi init {project}` first."
            )
        if not isinstance(entry, dict):
            raise ConfigError(
                f"Invalid `projects.{project}` in {config_path}; expected a table."
            )
        entry["chat_id"] = chat.chat_id
        write_config_fn(config, config_path)
        typer.echo(f"updated projects.{project}.chat_id = {chat.chat_id}")
        return

    typer.echo(f"chat_id = {chat.chat_id}")


def onboarding_paths() -> None:
    """Print all possible onboarding paths."""
    setup_logging_fn = cast(
        Callable[..., None],
        _resolve_cli_attr("setup_logging") or setup_logging,
    )
    onboarding_mod = cast(
        Any,
        _resolve_cli_attr("onboarding") or onboarding,
    )
    setup_logging_fn(debug=False, cache_logger_on_first_use=False)
    onboarding_mod.debug_onboarding_paths()


def _resolve_cli_attr(name: str) -> object | None:
    cli_module = sys.modules.get("takopi.cli")
    if cli_module is None:
        return None
    return getattr(cli_module, name, None)
