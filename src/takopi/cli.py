from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Callable
from pathlib import Path

import typer

from . import __version__
from .backends import EngineBackend
from .config import ConfigError, load_or_init_config, write_config
from .engines import get_backend, list_backends
from .lockfile import LockError, LockHandle, acquire_lock, token_fingerprint
from .logging import get_logger, setup_logging
from .router import AutoRouter, RunnerEntry
from .settings import (
    TakopiSettings,
    load_settings,
    load_settings_if_exists,
    require_telegram,
    validate_settings_data,
)
from .transports import SetupResult, get_transport, list_transports
from .utils.git import resolve_default_base, resolve_main_worktree_root

logger = get_logger(__name__)


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


def load_and_validate_config(
    path: str | Path | None = None,
) -> tuple[TakopiSettings, Path, str, int]:
    settings, config_path = load_settings(path)
    token, chat_id = require_telegram(settings, config_path)
    return settings, config_path, token, chat_id


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


def _default_engine_for_setup(override: str | None) -> str:
    if override:
        return override
    try:
        loaded = load_settings_if_exists()
    except ConfigError:
        return "codex"
    if loaded is None:
        return "codex"
    settings, config_path = loaded
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
    backends: list[EngineBackend],
) -> str:
    default_engine = override or settings.default_engine or "codex"
    if not isinstance(default_engine, str) or not default_engine.strip():
        raise ConfigError(
            f"Invalid `default_engine` in {config_path}; expected a non-empty string."
        )
    default_engine = default_engine.strip()
    backend_ids = {backend.id for backend in backends}
    if default_engine not in backend_ids:
        available = ", ".join(sorted(backend_ids))
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
    return any(issue.title == "create a config" for issue in setup.issues)


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
    setup_logging(debug=debug)
    lock_handle: LockHandle | None = None
    try:
        default_engine = _default_engine_for_setup(default_engine_override)
        engine_backend = get_backend(default_engine)
        transport_id = _resolve_transport_id(transport_override)
        transport_backend = get_transport(transport_id)
    except ConfigError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(code=1)
    if onboard:
        if not _should_run_interactive():
            typer.echo("error: --onboard requires a TTY", err=True)
            raise typer.Exit(code=1)
        if not transport_backend.interactive_setup(force=True):
            raise typer.Exit(code=1)
        default_engine = _default_engine_for_setup(default_engine_override)
        engine_backend = get_backend(default_engine)
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
                    default_engine = _default_engine_for_setup(default_engine_override)
                    engine_backend = get_backend(default_engine)
                    setup = transport_backend.check_setup(
                        engine_backend,
                        transport_override=transport_override,
                    )
            elif transport_backend.interactive_setup(force=False):
                default_engine = _default_engine_for_setup(default_engine_override)
                engine_backend = get_backend(default_engine)
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
        backends = list_backends()
        projects = settings.to_projects_config(
            config_path=config_path,
            engine_ids=[backend.id for backend in backends],
            reserved=("cancel",),
        )
        default_engine = _resolve_default_engine(
            override=default_engine_override,
            settings=settings,
            config_path=config_path,
            backends=backends,
        )
        router = _build_router(
            settings=settings,
            config_path=config_path,
            backends=backends,
            default_engine=default_engine,
        )
        lock_token = transport_backend.lock_token(
            settings=settings,
            config_path=config_path,
        )
        lock_handle = acquire_config_lock(config_path, lock_token)
        transport_backend.build_and_run(
            final_notify=final_notify,
            default_engine_override=default_engine_override,
            settings=settings,
            config_path=config_path,
            router=router,
            projects=projects,
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

    cwd = Path.cwd()
    project_path = resolve_main_worktree_root(cwd) or cwd
    default_alias = _default_alias_from_path(project_path)
    alias = _prompt_alias(alias, default_alias=default_alias)

    engine_ids = [backend.id for backend in list_backends()]
    settings = validate_settings_data(config, config_path=config_path)
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


def transports_cmd() -> None:
    """List available transport backends."""
    ids = list_transports()
    for transport_id in ids:
        typer.echo(transport_id)


app = typer.Typer(
    add_completion=False,
    invoke_without_command=True,
    help="Run takopi with auto-router (subcommands override the default engine).",
)


app.command(name="init")(init)
app.command(name="transports")(transports_cmd)


@app.callback()
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


def register_engine_commands() -> None:
    for backend in list_backends():
        help_text = f"Run with the {backend.id} engine."
        app.command(name=backend.id, help=help_text)(make_engine_cmd(backend.id))


register_engine_commands()


def main() -> None:
    app()


if __name__ == "__main__":
    main()
