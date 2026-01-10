from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Callable
from importlib.metadata import EntryPoint
from pathlib import Path

import typer

from . import __version__
from .backends import EngineBackend
from .config import ConfigError, load_or_init_config, write_config
from .config_migrations import migrate_config
from .commands import get_command
from .engines import get_backend, list_backend_ids
from .ids import RESERVED_COMMAND_IDS, RESERVED_ENGINE_IDS
from .lockfile import LockError, LockHandle, acquire_lock, token_fingerprint
from .logging import get_logger, setup_logging
from .router import AutoRouter, RunnerEntry
from .settings import (
    TakopiSettings,
    load_settings,
    load_settings_if_exists,
    validate_settings_data,
)
from .plugins import (
    COMMAND_GROUP,
    ENGINE_GROUP,
    TRANSPORT_GROUP,
    entrypoint_distribution_name,
    get_load_errors,
    is_entrypoint_allowed,
    list_entrypoints,
    normalize_allowlist,
)
from .transports import SetupResult, get_transport
from .transport_runtime import TransportRuntime
from .utils.git import resolve_default_base, resolve_main_worktree_root
from .telegram import onboarding

logger = get_logger(__name__)


def _load_settings_optional() -> tuple[TakopiSettings | None, Path | None]:
    try:
        loaded = load_settings_if_exists()
    except ConfigError:
        return None, None
    if loaded is None:
        return None, None
    return loaded


def _resolve_plugins_allowlist(
    settings: TakopiSettings | None,
) -> list[str] | None:
    if settings is None:
        return None
    enabled = [
        value.strip()
        for value in settings.plugins.enabled
        if isinstance(value, str) and value.strip()
    ]
    return enabled or None


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
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(
            f"Invalid `default_engine` in {config_path}; expected a non-empty string."
        )
    return value.strip()


def _resolve_default_engine(
    *,
    override: str | None,
    settings: TakopiSettings,
    config_path: Path,
    engine_ids: list[str],
) -> str:
    default_engine = override or settings.default_engine or "codex"
    if not isinstance(default_engine, str) or not default_engine.strip():
        raise ConfigError(
            f"Invalid `default_engine` in {config_path}; expected a non-empty string."
        )
    default_engine = default_engine.strip()
    if default_engine not in engine_ids:
        available = ", ".join(sorted(engine_ids))
        raise ConfigError(
            f"Unknown default engine {default_engine!r}. Available: {available}."
        )
    return default_engine


def _build_router(
    *,
    settings: TakopiSettings,
    config_path: Path,
    backends: list[EngineBackend],
    default_engine: str,
) -> AutoRouter:
    entries: list[RunnerEntry] = []
    warnings: list[str] = []

    for backend in backends:
        engine_id = backend.id
        issue: str | None = None
        engine_cfg: dict
        try:
            engine_cfg = settings.engine_config(engine_id, config_path=config_path)
        except ConfigError as exc:
            if engine_id == default_engine:
                raise
            issue = str(exc)
            engine_cfg = {}

        try:
            runner = backend.build_runner(engine_cfg, config_path)
        except Exception as exc:
            if engine_id == default_engine:
                raise
            issue = issue or str(exc)
            if engine_cfg:
                try:
                    runner = backend.build_runner({}, config_path)
                except Exception as fallback_exc:
                    warnings.append(f"{engine_id}: {issue or str(fallback_exc)}")
                    continue
            else:
                warnings.append(f"{engine_id}: {issue}")
                continue

        cmd = backend.cli_cmd or backend.id
        if shutil.which(cmd) is None:
            issue = issue or f"{cmd} not found on PATH"

        if issue and engine_id == default_engine:
            raise ConfigError(f"Default engine {engine_id!r} unavailable: {issue}")

        available = issue is None
        if issue and engine_id != default_engine:
            warnings.append(f"{engine_id}: {issue}")

        entries.append(
            RunnerEntry(
                engine=engine_id,
                runner=runner,
                available=available,
                issue=issue,
            )
        )

    for warning in warnings:
        logger.warning("setup.warning", issue=warning)

    return AutoRouter(entries=entries, default_engine=default_engine)


def _load_backends(
    *,
    engine_ids: list[str],
    allowlist: list[str] | None,
    default_engine: str,
) -> list[EngineBackend]:
    backends: list[EngineBackend] = []
    load_issues: list[str] = []
    for engine_id in engine_ids:
        try:
            backend = get_backend(engine_id, allowlist=allowlist)
        except ConfigError as exc:
            if engine_id == default_engine:
                raise
            load_issues.append(f"{engine_id}: {exc}")
            continue
        backends.append(backend)
    if not backends:
        raise ConfigError("No engine backends are available.")
    for issue in load_issues:
        logger.warning("setup.warning", issue=issue)
    return backends


def _config_path_display(path: Path) -> str:
    home = Path.home()
    try:
        return f"~/{path.relative_to(home)}"
    except ValueError:
        return str(path)


def _should_run_interactive() -> bool:
    if os.environ.get("TAKOPI_NO_INTERACTIVE"):
        return False
    return sys.stdin.isatty() and sys.stdout.isatty()


def _setup_needs_config(setup: SetupResult) -> bool:
    config_titles = {"create a config", "configure telegram"}
    return any(issue.title in config_titles for issue in setup.issues)


def _fail_missing_config(path: Path) -> None:
    display = _config_path_display(path)
    if path.exists():
        typer.echo(f"error: invalid takopi config at {display}", err=True)
    else:
        typer.echo(f"error: missing takopi config at {display}", err=True)


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
        os.environ.setdefault("TAKOPI_LOG_FORMAT", "json")
    setup_logging(debug=debug)
    lock_handle: LockHandle | None = None
    try:
        settings_hint, config_hint = _load_settings_optional()
        allowlist = _resolve_plugins_allowlist(settings_hint)
        default_engine = _default_engine_for_setup(
            default_engine_override,
            settings=settings_hint,
            config_path=config_hint,
        )
        engine_backend = get_backend(default_engine, allowlist=allowlist)
        transport_id = _resolve_transport_id(transport_override)
        transport_backend = get_transport(transport_id, allowlist=allowlist)
    except ConfigError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(code=1)
    if onboard:
        if not _should_run_interactive():
            typer.echo("error: --onboard requires a TTY", err=True)
            raise typer.Exit(code=1)
        if not transport_backend.interactive_setup(force=True):
            raise typer.Exit(code=1)
        settings_hint, config_hint = _load_settings_optional()
        allowlist = _resolve_plugins_allowlist(settings_hint)
        default_engine = _default_engine_for_setup(
            default_engine_override,
            settings=settings_hint,
            config_path=config_hint,
        )
        engine_backend = get_backend(default_engine, allowlist=allowlist)
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
                if run_onboard and transport_backend.interactive_setup(force=True):
                    settings_hint, config_hint = _load_settings_optional()
                    allowlist = _resolve_plugins_allowlist(settings_hint)
                    default_engine = _default_engine_for_setup(
                        default_engine_override,
                        settings=settings_hint,
                        config_path=config_hint,
                    )
                    engine_backend = get_backend(default_engine, allowlist=allowlist)
                    setup = transport_backend.check_setup(
                        engine_backend,
                        transport_override=transport_override,
                    )
            elif transport_backend.interactive_setup(force=False):
                settings_hint, config_hint = _load_settings_optional()
                allowlist = _resolve_plugins_allowlist(settings_hint)
                default_engine = _default_engine_for_setup(
                    default_engine_override,
                    settings=settings_hint,
                    config_path=config_hint,
                )
                engine_backend = get_backend(default_engine, allowlist=allowlist)
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
        allowlist = _resolve_plugins_allowlist(settings)
        engine_ids = list_backend_ids(allowlist=allowlist)
        projects = settings.to_projects_config(
            config_path=config_path,
            engine_ids=engine_ids,
            reserved=("cancel",),
        )
        default_engine = _resolve_default_engine(
            override=default_engine_override,
            settings=settings,
            config_path=config_path,
            engine_ids=engine_ids,
        )
        backends = _load_backends(
            engine_ids=engine_ids,
            allowlist=allowlist,
            default_engine=default_engine,
        )
        router = _build_router(
            settings=settings,
            config_path=config_path,
            backends=backends,
            default_engine=default_engine,
        )
        transport_config = settings.transport_config(
            settings.transport, config_path=config_path
        )
        lock_token = transport_backend.lock_token(
            transport_config=transport_config,
            config_path=config_path,
        )
        lock_handle = acquire_config_lock(config_path, lock_token)
        runtime = TransportRuntime(
            router=router,
            projects=projects,
            allowlist=allowlist,
            config_path=config_path,
            plugin_configs=settings.plugins.model_extra,
        )
        transport_backend.build_and_run(
            final_notify=final_notify,
            default_engine_override=default_engine_override,
            config_path=config_path,
            transport_config=transport_config,
            runtime=runtime,
        )
    except ConfigError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(code=1)
    except KeyboardInterrupt:
        logger.info("shutdown.interrupted")
        raise typer.Exit(code=130)
    finally:
        if lock_handle is not None:
            lock_handle.release()


def _prompt_alias(value: str | None, *, default_alias: str | None = None) -> str:
    if value is not None:
        alias = value
    elif default_alias:
        alias = typer.prompt("project alias", default=default_alias)
    else:
        alias = typer.prompt("project alias")
    alias = alias.strip()
    if not alias:
        typer.echo("error: project alias cannot be empty", err=True)
        raise typer.Exit(code=1)
    return alias


def _default_alias_from_path(path: Path) -> str | None:
    name = path.name
    if not name:
        return None
    if name.endswith(".git"):
        name = name[: -len(".git")]
    return name or None


def _ensure_projects_table(config: dict, config_path: Path) -> dict:
    projects = config.get("projects")
    if projects is None:
        projects = {}
        config["projects"] = projects
    if not isinstance(projects, dict):
        raise ConfigError(f"Invalid `projects` in {config_path}; expected a table.")
    return projects


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
    config, config_path = load_or_init_config()
    if config_path.exists():
        applied = migrate_config(config, config_path=config_path)
        if applied:
            write_config(config, config_path)

    cwd = Path.cwd()
    project_path = resolve_main_worktree_root(cwd) or cwd
    default_alias = _default_alias_from_path(project_path)
    alias = _prompt_alias(alias, default_alias=default_alias)

    settings = validate_settings_data(config, config_path=config_path)
    allowlist = _resolve_plugins_allowlist(settings)
    engine_ids = list_backend_ids(allowlist=allowlist)
    projects_cfg = settings.to_projects_config(
        config_path=config_path,
        engine_ids=engine_ids,
        reserved=("cancel",),
    )

    alias_key = alias.lower()
    if alias_key in {engine.lower() for engine in engine_ids}:
        raise ConfigError(
            f"Invalid project alias {alias!r}; aliases must not match engine ids."
        )
    if alias_key == "cancel":
        raise ConfigError(
            f"Invalid project alias {alias!r}; aliases must not match reserved commands."
        )

    existing = projects_cfg.projects.get(alias_key)
    if existing is not None:
        overwrite = typer.confirm(
            f"project {existing.alias!r} already exists, overwrite?",
            default=False,
        )
        if not overwrite:
            raise typer.Exit(code=1)

    projects = _ensure_projects_table(config, config_path)
    if existing is not None and existing.alias in projects:
        projects.pop(existing.alias, None)

    default_engine = settings.default_engine
    worktree_base = resolve_default_base(project_path)

    entry: dict[str, object] = {
        "path": str(project_path),
        "worktrees_dir": ".worktrees",
        "default_engine": default_engine,
    }
    if worktree_base:
        entry["worktree_base"] = worktree_base

    projects[alias] = entry
    if default:
        config["default_project"] = alias

    write_config(config, config_path)
    typer.echo(f"saved project {alias!r} to {_config_path_display(config_path)}")


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
            if tg.bot_token is not None:
                token = tg.bot_token.get_secret_value().strip() or None
    chat = onboarding.capture_chat_id(token=token)
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


def _print_entrypoints(
    label: str, entrypoints: list[EntryPoint], *, allowlist: set[str] | None
) -> None:
    typer.echo(f"{label}:")
    if not entrypoints:
        typer.echo("  (none)")
        return
    for ep in entrypoints:
        dist = entrypoint_distribution_name(ep) or "unknown"
        status = ""
        if allowlist is not None:
            allowed = is_entrypoint_allowed(ep, allowlist)
            status = " enabled" if allowed else " disabled"
        typer.echo(f"  {ep.name} ({dist}){status}")


def plugins_cmd(
    load: bool = typer.Option(
        False,
        "--load/--no-load",
        help="Load plugins to validate and surface import errors.",
    ),
) -> None:
    """List discovered plugins and optionally validate them."""
    settings_hint, _ = _load_settings_optional()
    allowlist = _resolve_plugins_allowlist(settings_hint)

    allowlist_set = normalize_allowlist(allowlist)
    engine_eps = list_entrypoints(
        ENGINE_GROUP,
        reserved_ids=RESERVED_ENGINE_IDS,
    )
    transport_eps = list_entrypoints(TRANSPORT_GROUP)
    command_eps = list_entrypoints(
        COMMAND_GROUP,
        reserved_ids=RESERVED_COMMAND_IDS,
    )

    _print_entrypoints("engine backends", engine_eps, allowlist=allowlist_set)
    _print_entrypoints("transport backends", transport_eps, allowlist=allowlist_set)
    _print_entrypoints("command backends", command_eps, allowlist=allowlist_set)

    if load:
        for ep in engine_eps:
            if allowlist_set is not None and not is_entrypoint_allowed(
                ep, allowlist_set
            ):
                continue
            try:
                get_backend(ep.name, allowlist=allowlist)
            except ConfigError:
                continue
        for ep in transport_eps:
            if allowlist_set is not None and not is_entrypoint_allowed(
                ep, allowlist_set
            ):
                continue
            try:
                get_transport(ep.name, allowlist=allowlist)
            except ConfigError:
                continue
        for ep in command_eps:
            if allowlist_set is not None and not is_entrypoint_allowed(
                ep, allowlist_set
            ):
                continue
            try:
                get_command(ep.name, allowlist=allowlist)
            except ConfigError:
                continue

    errors = get_load_errors()
    if errors:
        typer.echo("errors:")
        for err in errors:
            group = err.group
            if group == ENGINE_GROUP:
                group = "engine"
            elif group == TRANSPORT_GROUP:
                group = "transport"
            elif group == COMMAND_GROUP:
                group = "command"
            dist = err.distribution or "unknown"
            typer.echo(f"  {group} {err.name} ({dist}): {err.error}")


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
        help="Run takopi with auto-router (subcommands override the default engine).",
    )
    app.command(name="init")(init)
    app.command(name="chat-id")(chat_id)
    app.command(name="plugins")(plugins_cmd)
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
