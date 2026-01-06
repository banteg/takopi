from __future__ import annotations

import os
import sys
from collections.abc import Callable
from pathlib import Path

import anyio
import typer

from . import __version__
from .backends import EngineBackend
from .config import ConfigError
from .engines import get_backend, list_backends
from .lockfile import LockError, LockHandle, acquire_lock, token_fingerprint
from .logging import get_logger, setup_logging
from .router_factory import RouterFactory
from .telegram.bridge import (
    TelegramBridgeConfig,
    TelegramPresenter,
    TelegramTransport,
    run_main_loop,
)
from .telegram.client import TelegramClient
from .telegram.config import load_telegram_config
from .telegram.onboarding import SetupResult, check_setup, interactive_setup
from .runner_bridge import ExecBridgeConfig

logger = get_logger(__name__)


def _print_version_and_exit() -> None:
    typer.echo(__version__)
    raise typer.Exit()


def _version_callback(value: bool) -> None:
    if value:
        _print_version_and_exit()


def load_and_validate_config(
    path: str | Path | None = None,
) -> tuple[dict, Path, str, int]:
    config, config_path = load_telegram_config(path)
    try:
        token = config["bot_token"]
    except KeyError:
        raise ConfigError(f"Missing key `bot_token` in {config_path}.") from None
    if not isinstance(token, str) or not token.strip():
        raise ConfigError(
            f"Invalid `bot_token` in {config_path}; expected a non-empty string."
        ) from None
    try:
        chat_id_value = config["chat_id"]
    except KeyError:
        raise ConfigError(f"Missing key `chat_id` in {config_path}.") from None
    if isinstance(chat_id_value, bool) or not isinstance(chat_id_value, int):
        raise ConfigError(
            f"Invalid `chat_id` in {config_path}; expected an integer."
        ) from None
    return config, config_path, token.strip(), chat_id_value


def acquire_config_lock(config_path: Path, token: str) -> LockHandle:
    try:
        return acquire_lock(
            config_path=config_path,
            token_fingerprint=token_fingerprint(token),
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
        config, config_path = load_telegram_config()
    except ConfigError:
        return "codex"
    value = config.get("default_engine")
    if value is None:
        return "codex"
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(
            f"Invalid `default_engine` in {config_path}; expected a non-empty string."
        )
    return value.strip()


def _format_startup_msg(
    router_factory: RouterFactory,
    active_profile: str | None,
    startup_pwd: str,
) -> str:
    """Format the startup message with profile info."""
    router = router_factory.build_router(active_profile)
    available_engines = [entry.engine for entry in router.available_entries]
    missing_engines = [entry.engine for entry in router.entries if not entry.available]
    engine_list = ", ".join(available_engines) if available_engines else "none"
    if missing_engines:
        engine_list = f"{engine_list} (not installed: {', '.join(missing_engines)})"

    lines = [
        f"\N{OCTOPUS} **takopi is ready**\n",
        f"default: `{router.default_engine}`  ",
        f"agents: `{engine_list}`  ",
    ]

    if router_factory.has_profiles:
        profile_display = active_profile or "(none)"
        profiles_list = ", ".join(router_factory.profile_names)
        lines.append(f"profile: `{profile_display}`  ")
        lines.append(f"profiles: `{profiles_list}`  ")

    lines.append(f"working in: `{startup_pwd}`")
    return "\n".join(lines)


def _parse_bridge_config(
    *,
    final_notify: bool,
    default_engine_override: str | None,
    config: dict,
    config_path: Path,
    token: str,
    chat_id: int,
) -> TelegramBridgeConfig:
    startup_pwd = os.getcwd()

    router_factory = RouterFactory.create(
        config=config,
        config_path=config_path,
        default_engine_override=default_engine_override,
    )

    # Build initial router with no profile
    router = router_factory.build_router(None)
    startup_msg = _format_startup_msg(router_factory, None, startup_pwd)

    bot = TelegramClient(token)
    transport = TelegramTransport(bot)
    presenter = TelegramPresenter()
    exec_cfg = ExecBridgeConfig(
        transport=transport,
        presenter=presenter,
        final_notify=final_notify,
    )

    return TelegramBridgeConfig(
        bot=bot,
        router=router,
        router_factory=router_factory,
        chat_id=chat_id,
        startup_msg=startup_msg,
        startup_pwd=startup_pwd,
        exec_cfg=exec_cfg,
    )


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
    typer.echo(f"error: missing takopi config at {display}", err=True)


def _run_auto_router(
    *,
    default_engine_override: str | None,
    final_notify: bool,
    debug: bool,
    onboard: bool,
) -> None:
    setup_logging(debug=debug)
    lock_handle: LockHandle | None = None
    try:
        default_engine = _default_engine_for_setup(default_engine_override)
        backend = get_backend(default_engine)
    except ConfigError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(code=1)
    if onboard:
        if not _should_run_interactive():
            typer.echo("error: --onboard requires a TTY", err=True)
            raise typer.Exit(code=1)
        if not interactive_setup(force=True):
            raise typer.Exit(code=1)
        default_engine = _default_engine_for_setup(default_engine_override)
        backend = get_backend(default_engine)
    setup = check_setup(backend)
    if not setup.ok:
        if _setup_needs_config(setup) and _should_run_interactive():
            if interactive_setup(force=False):
                default_engine = _default_engine_for_setup(default_engine_override)
                backend = get_backend(default_engine)
                setup = check_setup(backend)
        if not setup.ok:
            if _setup_needs_config(setup):
                _fail_missing_config(setup.config_path)
            else:
                first = setup.issues[0]
                typer.echo(f"error: {first.title}", err=True)
            raise typer.Exit(code=1)
    try:
        config, config_path, token, chat_id = load_and_validate_config()
        lock_handle = acquire_config_lock(config_path, token)
        cfg = _parse_bridge_config(
            final_notify=final_notify,
            default_engine_override=default_engine_override,
            config=config,
            config_path=config_path,
            token=token,
            chat_id=chat_id,
        )
        anyio.run(run_main_loop, cfg)
    except ConfigError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(code=1)
    except KeyboardInterrupt:
        logger.info("shutdown.interrupted")
        raise typer.Exit(code=130)
    finally:
        if lock_handle is not None:
            lock_handle.release()


app = typer.Typer(
    add_completion=False,
    invoke_without_command=True,
    help="Run takopi with auto-router (subcommands override the default engine).",
)


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
        debug: bool = typer.Option(
            False,
            "--debug/--no-debug",
            help="Log engine JSONL, Telegram requests, and rendered messages.",
        ),
    ) -> None:
        _run_auto_router(
            default_engine_override=engine_id,
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
