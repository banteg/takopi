from __future__ import annotations

# ruff: noqa: F401

from collections.abc import Callable
import sys
from pathlib import Path

import typer

from .. import __version__
from ..config import (
    ConfigError,
    HOME_CONFIG_PATH,
    load_or_init_config,
    write_config,
)
from ..config_migrations import migrate_config
from ..commands import get_command
from ..engines import get_backend, list_backend_ids
from ..ids import RESERVED_CHAT_COMMANDS, RESERVED_COMMAND_IDS, RESERVED_ENGINE_IDS
from ..lockfile import LockError, LockHandle, acquire_lock, token_fingerprint
from ..logging import setup_logging
from ..runtime_loader import build_runtime_spec, resolve_plugins_allowlist
from ..settings import (
    TakopiSettings,
    load_settings,
    load_settings_if_exists,
    validate_settings_data,
)
from ..plugins import (
    COMMAND_GROUP,
    ENGINE_GROUP,
    TRANSPORT_GROUP,
    entrypoint_distribution_name,
    get_load_errors,
    is_entrypoint_allowed,
    list_entrypoints,
    normalize_allowlist,
)
from ..transports import get_transport
from ..utils.git import resolve_default_base, resolve_main_worktree_root
from ..telegram import onboarding
from ..telegram.client import TelegramClient
from ..telegram.topics import _validate_topics_setup_for
from .doctor import (
    DoctorCheck,
    DoctorStatus,
    _doctor_file_checks,
    _doctor_telegram_checks,
    _doctor_voice_checks,
    run_doctor,
)
from .init import (
    _default_alias_from_path,
    _ensure_projects_table,
    _prompt_alias,
    run_init,
)
from .onboarding_cmd import chat_id, onboarding_paths
from .plugins import plugins_cmd
from .run import (
    _default_engine_for_setup,
    _print_version_and_exit,
    _resolve_setup_engine,
    _resolve_transport_id,
    _run_auto_router,
    _setup_needs_config,
    _should_run_interactive,
    _version_callback,
    acquire_config_lock,
    app_main,
    make_engine_cmd,
)
from .config import (
    _CONFIG_PATH_OPTION,
    _config_path_display,
    _exit_config_error,
    _fail_missing_config,
    _flatten_config,
    _load_config_or_exit,
    _normalized_value_from_settings,
    _parse_key_path,
    _parse_value,
    _resolve_config_path_override,
    _toml_literal,
    config_get,
    config_list,
    config_path_cmd,
    config_set,
    config_unset,
)


def _load_settings_optional() -> tuple[TakopiSettings | None, Path | None]:
    try:
        loaded = load_settings_if_exists()
    except ConfigError:
        return None, None
    if loaded is None:
        return None, None
    return loaded


def init(
    alias: str | None = typer.Argument(
        None, help="Project alias (used as /alias in messages)."
    ),
    default: bool = typer.Option(
        False,
        "--default",
        help="Set this project as the default_project.",
    ),
) -> None:
    """Register the current repo as a Takopi project."""
    run_init(
        alias=alias,
        default=default,
        load_or_init_config_fn=load_or_init_config,
        resolve_main_worktree_root_fn=resolve_main_worktree_root,
        resolve_default_base_fn=resolve_default_base,
        list_backend_ids_fn=list_backend_ids,
        resolve_plugins_allowlist_fn=resolve_plugins_allowlist,
    )


def doctor() -> None:
    """Run configuration checks for the active transport."""
    setup_logging(debug=False, cache_logger_on_first_use=False)
    run_doctor(
        load_settings_fn=load_settings,
        telegram_checks=_doctor_telegram_checks,
        file_checks=_doctor_file_checks,
        voice_checks=_doctor_voice_checks,
    )


def _engine_ids_for_cli() -> list[str]:
    allowlist: list[str] | None = None
    try:
        config, _ = load_or_init_config()
    except ConfigError:
        return list_backend_ids()
    raw_plugins = config.get("plugins")
    if isinstance(raw_plugins, dict):
        enabled = raw_plugins.get("enabled")
        if isinstance(enabled, list):
            allowlist = [
                value.strip()
                for value in enabled
                if isinstance(value, str) and value.strip()
            ]
            if not allowlist:
                allowlist = None
    return list_backend_ids(allowlist=allowlist)


def create_app() -> typer.Typer:
    app = typer.Typer(
        add_completion=False,
        invoke_without_command=True,
        help="Telegram bridge for coding agents. Docs: https://takopi.dev/",
    )
    config_app = typer.Typer(help="Read and modify takopi config.")
    config_app.command(name="path")(config_path_cmd)
    config_app.command(name="list")(config_list)
    config_app.command(name="get")(config_get)
    config_app.command(name="set")(config_set)
    config_app.command(name="unset")(config_unset)
    app.command(name="init")(init)
    app.command(name="chat-id")(chat_id)
    app.command(name="doctor")(doctor)
    app.command(name="onboarding-paths")(onboarding_paths)
    app.command(name="plugins")(plugins_cmd)
    app.add_typer(config_app, name="config")
    app.callback()(app_main)
    for engine_id in _engine_ids_for_cli():
        help_text = f"Run with the {engine_id} engine."
        app.command(name=engine_id, help=help_text)(make_engine_cmd(engine_id))
    return app


def main() -> None:
    app = create_app()
    app()


if __name__ == "__main__":
    main()
