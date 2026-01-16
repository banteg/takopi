from __future__ import annotations

# ruff: noqa: F401

import os
import sys
from collections.abc import Callable
from pathlib import Path

import anyio
from functools import partial
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
from ..backends import EngineBackend
from ..engines import get_backend, list_backend_ids
from ..ids import RESERVED_CHAT_COMMANDS, RESERVED_COMMAND_IDS, RESERVED_ENGINE_IDS
from ..lockfile import LockError, LockHandle, acquire_lock, token_fingerprint
from ..logging import get_logger, setup_logging
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
from ..transports import SetupResult, get_transport
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
from .plugins import plugins_cmd
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

logger = get_logger(__name__)


def _load_settings_optional() -> tuple[TakopiSettings | None, Path | None]:
    try:
        loaded = load_settings_if_exists()
    except ConfigError:
        return None, None
    if loaded is None:
        return None, None
    return loaded


def _print_version_and_exit() -> None:
    typer.echo(__version__)
    raise typer.Exit()


def _version_callback(value: bool) -> None:
    if value:
        _print_version_and_exit()


def _resolve_transport_id(override: str | None) -> str:
    if override is not None:
        value = override.strip()
        if not value:
            raise ConfigError("Invalid `--transport`; expected a non-empty string.")
        return value
    try:
        config, _ = load_or_init_config()
    except ConfigError:
        return "telegram"
    raw = config.get("transport")
    if not isinstance(raw, str) or not raw.strip():
        return "telegram"
    return raw.strip()


def acquire_config_lock(config_path: Path, token: str | None) -> LockHandle:
    fingerprint = token_fingerprint(token) if token else None
    try:
        return acquire_lock(
            config_path=config_path,
            token_fingerprint=fingerprint,
        )
    except LockError as exc:
        lines = str(exc).splitlines()
        if lines:
            typer.echo(lines[0], err=True)
            if len(lines) > 1:
                typer.echo("\n".join(lines[1:]), err=True)
        else:
            typer.echo("error: unknown error", err=True)
        raise typer.Exit(code=1) from exc


def _default_engine_for_setup(
    override: str | None,
    *,
    settings: TakopiSettings | None,
    config_path: Path | None,
) -> str:
    if override:
        return override
    if settings is None or config_path is None:
        return "codex"
    value = settings.default_engine
    return value


def _resolve_setup_engine(
    default_engine_override: str | None,
) -> tuple[
    TakopiSettings | None,
    Path | None,
    list[str] | None,
    str,
    EngineBackend,
]:
    settings_hint, config_hint = _load_settings_optional()
    allowlist = resolve_plugins_allowlist(settings_hint)
    default_engine = _default_engine_for_setup(
        default_engine_override,
        settings=settings_hint,
        config_path=config_hint,
    )
    engine_backend = get_backend(default_engine, allowlist=allowlist)
    return settings_hint, config_hint, allowlist, default_engine, engine_backend


def _should_run_interactive() -> bool:
    if os.environ.get("TAKOPI_NO_INTERACTIVE"):
        return False
    return sys.stdin.isatty() and sys.stdout.isatty()


def _setup_needs_config(setup: SetupResult) -> bool:
    config_titles = {"create a config", "configure telegram"}
    return any(issue.title in config_titles for issue in setup.issues)


def _run_auto_router(
    *,
    default_engine_override: str | None,
    transport_override: str | None,
    final_notify: bool,
    debug: bool,
    onboard: bool,
) -> None:
    if debug:
        os.environ.setdefault("TAKOPI_LOG_FILE", "debug.log")
    setup_logging(debug=debug)
    lock_handle: LockHandle | None = None
    try:
        (
            settings_hint,
            config_hint,
            allowlist,
            default_engine,
            engine_backend,
        ) = _resolve_setup_engine(default_engine_override)
        transport_id = _resolve_transport_id(transport_override)
        transport_backend = get_transport(transport_id, allowlist=allowlist)
    except ConfigError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(code=1) from e
    if onboard:
        if not _should_run_interactive():
            typer.echo("error: --onboard requires a TTY", err=True)
            raise typer.Exit(code=1)
        if not anyio.run(partial(transport_backend.interactive_setup, force=True)):
            raise typer.Exit(code=1)
        (
            settings_hint,
            config_hint,
            allowlist,
            default_engine,
            engine_backend,
        ) = _resolve_setup_engine(default_engine_override)
    setup = transport_backend.check_setup(
        engine_backend,
        transport_override=transport_override,
    )
    if not setup.ok:
        if _setup_needs_config(setup) and _should_run_interactive():
            if setup.config_path.exists():
                display = _config_path_display(setup.config_path)
                run_onboard = typer.confirm(
                    f"config at {display} is missing/invalid for "
                    f"{transport_backend.id}, run onboarding now?",
                    default=False,
                )
                if run_onboard and anyio.run(
                    partial(transport_backend.interactive_setup, force=True)
                ):
                    (
                        settings_hint,
                        config_hint,
                        allowlist,
                        default_engine,
                        engine_backend,
                    ) = _resolve_setup_engine(default_engine_override)
                    setup = transport_backend.check_setup(
                        engine_backend,
                        transport_override=transport_override,
                    )
            elif anyio.run(partial(transport_backend.interactive_setup, force=False)):
                (
                    settings_hint,
                    config_hint,
                    allowlist,
                    default_engine,
                    engine_backend,
                ) = _resolve_setup_engine(default_engine_override)
                setup = transport_backend.check_setup(
                    engine_backend,
                    transport_override=transport_override,
                )
        if not setup.ok:
            if _setup_needs_config(setup):
                _fail_missing_config(setup.config_path)
            else:
                first = setup.issues[0]
                typer.echo(f"error: {first.title}", err=True)
            raise typer.Exit(code=1)
    try:
        settings, config_path = load_settings()
        if transport_override and transport_override != settings.transport:
            settings = settings.model_copy(update={"transport": transport_override})
        spec = build_runtime_spec(
            settings=settings,
            config_path=config_path,
            default_engine_override=default_engine_override,
            reserved=RESERVED_CHAT_COMMANDS,
        )
        if settings.transport == "telegram":
            transport_config = settings.transports.telegram
        else:
            transport_config = settings.transport_config(
                settings.transport, config_path=config_path
            )
        lock_token = transport_backend.lock_token(
            transport_config=transport_config,
            _config_path=config_path,
        )
        lock_handle = acquire_config_lock(config_path, lock_token)
        runtime = spec.to_runtime(config_path=config_path)
        transport_backend.build_and_run(
            final_notify=final_notify,
            default_engine_override=default_engine_override,
            config_path=config_path,
            transport_config=transport_config,
            runtime=runtime,
        )
    except ConfigError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(code=1) from e
    except KeyboardInterrupt:
        logger.info("shutdown.interrupted")
        raise typer.Exit(code=130) from None
    finally:
        if lock_handle is not None:
            lock_handle.release()


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
    setup_logging(debug=False, cache_logger_on_first_use=False)
    if token is None:
        settings, _ = _load_settings_optional()
        if settings is not None:
            tg = settings.transports.telegram
            token = tg.bot_token or None
    chat = anyio.run(partial(onboarding.capture_chat_id, token=token))
    if chat is None:
        raise typer.Exit(code=1)
    if project:
        project = project.strip()
        if not project:
            raise ConfigError("Invalid `--project`; expected a non-empty string.")

        config, config_path = load_or_init_config()
        if config_path.exists():
            applied = migrate_config(config, config_path=config_path)
            if applied:
                write_config(config, config_path)

        projects = _ensure_projects_table(config, config_path)
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
        write_config(config, config_path)
        typer.echo(f"updated projects.{project}.chat_id = {chat.chat_id}")
        return

    typer.echo(f"chat_id = {chat.chat_id}")


def onboarding_paths() -> None:
    """Print all possible onboarding paths."""
    setup_logging(debug=False, cache_logger_on_first_use=False)
    onboarding.debug_onboarding_paths()


def doctor() -> None:
    """Run configuration checks for the active transport."""
    setup_logging(debug=False, cache_logger_on_first_use=False)
    run_doctor(
        load_settings_fn=load_settings,
        telegram_checks=_doctor_telegram_checks,
        file_checks=_doctor_file_checks,
        voice_checks=_doctor_voice_checks,
    )


def app_main(
    ctx: typer.Context,
    version: bool = typer.Option(
        False,
        "--version",
        help="Show the version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
    final_notify: bool = typer.Option(
        True,
        "--final-notify/--no-final-notify",
        help="Send the final response as a new message (not an edit).",
    ),
    onboard: bool = typer.Option(
        False,
        "--onboard/--no-onboard",
        help="Run the interactive setup wizard before starting.",
    ),
    transport: str | None = typer.Option(
        None,
        "--transport",
        help="Override the transport backend id.",
    ),
    debug: bool = typer.Option(
        False,
        "--debug/--no-debug",
        help="Log engine JSONL, Telegram requests, and rendered messages.",
    ),
) -> None:
    """Takopi CLI."""
    if ctx.invoked_subcommand is None:
        _run_auto_router(
            default_engine_override=None,
            transport_override=transport,
            final_notify=final_notify,
            debug=debug,
            onboard=onboard,
        )
        raise typer.Exit()


def make_engine_cmd(engine_id: str) -> Callable[..., None]:
    def _cmd(
        final_notify: bool = typer.Option(
            True,
            "--final-notify/--no-final-notify",
            help="Send the final response as a new message (not an edit).",
        ),
        onboard: bool = typer.Option(
            False,
            "--onboard/--no-onboard",
            help="Run the interactive setup wizard before starting.",
        ),
        transport: str | None = typer.Option(
            None,
            "--transport",
            help="Override the transport backend id.",
        ),
        debug: bool = typer.Option(
            False,
            "--debug/--no-debug",
            help="Log engine JSONL, Telegram requests, and rendered messages.",
        ),
    ) -> None:
        _run_auto_router(
            default_engine_override=engine_id,
            transport_override=transport,
            final_notify=final_notify,
            debug=debug,
            onboard=onboard,
        )

    _cmd.__name__ = f"run_{engine_id}"
    return _cmd


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
