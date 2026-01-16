from __future__ import annotations

import os
import sys
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any, cast

import anyio
import typer

from .. import __version__
from ..backends import EngineBackend
from ..config import ConfigError, load_or_init_config
from ..engines import get_backend
from ..ids import RESERVED_CHAT_COMMANDS
from ..lockfile import LockError, LockHandle, acquire_lock, token_fingerprint
from ..logging import get_logger, setup_logging
from ..runtime_loader import build_runtime_spec, resolve_plugins_allowlist
from ..settings import TakopiSettings, load_settings, load_settings_if_exists
from ..transports import SetupResult, get_transport
from .config import _config_path_display, _fail_missing_config

logger = get_logger(__name__)


def _load_settings_optional() -> tuple[TakopiSettings | None, Path | None]:
    try:
        loaded = load_settings_if_exists()
    except ConfigError:
        return None, None
    if loaded is None:
        return None, None
    return loaded


def _resolve_transport_id(override: str | None) -> str:
    if override is not None:
        value = override.strip()
        if not value:
            raise ConfigError("Invalid `--transport`; expected a non-empty string.")
        return value
    load_or_init_config_fn = cast(
        Callable[[], tuple[dict, Path]],
        _resolve_cli_attr("load_or_init_config") or load_or_init_config,
    )
    try:
        config, _ = load_or_init_config_fn()
    except ConfigError:
        return "telegram"
    raw = config.get("transport")
    if not isinstance(raw, str) or not raw.strip():
        return "telegram"
    return raw.strip()


def acquire_config_lock(config_path: Path, token: str | None) -> LockHandle:
    fingerprint = token_fingerprint(token) if token else None
    acquire_lock_fn = cast(
        Callable[..., LockHandle],
        _resolve_cli_attr("acquire_lock") or acquire_lock,
    )
    try:
        return acquire_lock_fn(
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
    load_settings_optional_fn = cast(
        Callable[[], tuple[TakopiSettings | None, Path | None]],
        _resolve_cli_attr("_load_settings_optional") or _load_settings_optional,
    )
    resolve_plugins_allowlist_fn = cast(
        Callable[[TakopiSettings | None], list[str] | None],
        _resolve_cli_attr("resolve_plugins_allowlist") or resolve_plugins_allowlist,
    )
    default_engine_for_setup_fn = cast(
        Callable[..., str],
        _resolve_cli_attr("_default_engine_for_setup") or _default_engine_for_setup,
    )
    get_backend_fn = cast(
        Callable[..., EngineBackend],
        _resolve_cli_attr("get_backend") or get_backend,
    )

    settings_hint, config_hint = load_settings_optional_fn()
    allowlist = resolve_plugins_allowlist_fn(settings_hint)
    default_engine = default_engine_for_setup_fn(
        default_engine_override,
        settings=settings_hint,
        config_path=config_hint,
    )
    engine_backend = get_backend_fn(default_engine, allowlist=allowlist)
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
    setup_logging_fn = cast(
        Callable[..., None],
        _resolve_cli_attr("setup_logging") or setup_logging,
    )
    resolve_setup_engine_fn = cast(
        Callable[
            [str | None],
            tuple[
                TakopiSettings | None,
                Path | None,
                list[str] | None,
                str,
                EngineBackend,
            ],
        ],
        _resolve_cli_attr("_resolve_setup_engine") or _resolve_setup_engine,
    )
    resolve_transport_id_fn = cast(
        Callable[[str | None], str],
        _resolve_cli_attr("_resolve_transport_id") or _resolve_transport_id,
    )
    get_transport_fn = cast(
        Callable[..., Any],
        _resolve_cli_attr("get_transport") or get_transport,
    )
    should_run_interactive_fn = cast(
        Callable[[], bool],
        _resolve_cli_attr("_should_run_interactive") or _should_run_interactive,
    )
    setup_needs_config_fn = cast(
        Callable[[SetupResult], bool],
        _resolve_cli_attr("_setup_needs_config") or _setup_needs_config,
    )
    config_path_display_fn = cast(
        Callable[[Path], str],
        _resolve_cli_attr("_config_path_display") or _config_path_display,
    )
    fail_missing_config_fn = cast(
        Callable[[Path], None],
        _resolve_cli_attr("_fail_missing_config") or _fail_missing_config,
    )
    load_settings_fn = cast(
        Callable[[], tuple[TakopiSettings, Path]],
        _resolve_cli_attr("load_settings") or load_settings,
    )
    build_runtime_spec_fn = cast(
        Callable[..., Any],
        _resolve_cli_attr("build_runtime_spec") or build_runtime_spec,
    )
    acquire_config_lock_fn = cast(
        Callable[[Path, str | None], LockHandle],
        _resolve_cli_attr("acquire_config_lock") or acquire_config_lock,
    )

    if debug:
        os.environ.setdefault("TAKOPI_LOG_FILE", "debug.log")
    setup_logging_fn(debug=debug)
    lock_handle: LockHandle | None = None
    try:
        (
            settings_hint,
            config_hint,
            allowlist,
            default_engine,
            engine_backend,
        ) = resolve_setup_engine_fn(default_engine_override)
        transport_id = resolve_transport_id_fn(transport_override)
        transport_backend = get_transport_fn(transport_id, allowlist=allowlist)
    except ConfigError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if onboard:
        if not should_run_interactive_fn():
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
        ) = resolve_setup_engine_fn(default_engine_override)
    setup = transport_backend.check_setup(
        engine_backend,
        transport_override=transport_override,
    )
    if not setup.ok:
        if setup_needs_config_fn(setup) and should_run_interactive_fn():
            if setup.config_path.exists():
                display = config_path_display_fn(setup.config_path)
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
                    ) = resolve_setup_engine_fn(default_engine_override)
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
                ) = resolve_setup_engine_fn(default_engine_override)
                setup = transport_backend.check_setup(
                    engine_backend,
                    transport_override=transport_override,
                )
        if not setup.ok:
            if setup_needs_config_fn(setup):
                fail_missing_config_fn(setup.config_path)
            else:
                first = setup.issues[0]
                typer.echo(f"error: {first.title}", err=True)
            raise typer.Exit(code=1)
    try:
        settings, config_path = load_settings_fn()
        if transport_override and transport_override != settings.transport:
            settings = settings.model_copy(update={"transport": transport_override})
        spec = build_runtime_spec_fn(
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
        lock_handle = acquire_config_lock_fn(config_path, lock_token)
        runtime = spec.to_runtime(config_path=config_path)
        transport_backend.build_and_run(
            final_notify=final_notify,
            default_engine_override=default_engine_override,
            config_path=config_path,
            transport_config=transport_config,
            runtime=runtime,
        )
    except ConfigError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    except KeyboardInterrupt:
        logger.info("shutdown.interrupted")
        raise typer.Exit(code=130) from None
    finally:
        if lock_handle is not None:
            lock_handle.release()


def _print_version_and_exit() -> None:
    typer.echo(__version__)
    raise typer.Exit()


def _version_callback(value: bool) -> None:
    if value:
        _print_version_and_exit()


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
        run_auto_router = cast(
            Callable[..., None],
            _resolve_cli_attr("_run_auto_router") or _run_auto_router,
        )
        run_auto_router(
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
        run_auto_router = cast(
            Callable[..., None],
            _resolve_cli_attr("_run_auto_router") or _run_auto_router,
        )
        run_auto_router(
            default_engine_override=engine_id,
            transport_override=transport,
            final_notify=final_notify,
            debug=debug,
            onboard=onboard,
        )

    _cmd.__name__ = f"run_{engine_id}"
    return _cmd


def _resolve_cli_attr(name: str) -> object | None:
    cli_module = sys.modules.get("takopi.cli")
    if cli_module is None:
        return None
    return getattr(cli_module, name, None)
