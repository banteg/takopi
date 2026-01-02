from __future__ import annotations

import logging
import os
import shutil
from collections.abc import Callable
from pathlib import Path

import anyio
import typer

from . import __version__
from .backends import EngineBackend
from .bridge import BridgeConfig, run_main_loop
from .config import ConfigError, load_telegram_config
from .engines import get_backend, get_engine_config, list_backends
from .logging import setup_logging
from .onboarding import check_setup, render_setup_guide
from .router import AutoRouter, RunnerEntry
from .telegram import TelegramClient

logger = logging.getLogger(__name__)


def _print_version_and_exit() -> None:
    typer.echo(__version__)
    raise typer.Exit()


def _version_callback(value: bool) -> None:
    if value:
        _print_version_and_exit()


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


def _resolve_default_engine(
    *,
    override: str | None,
    config: dict,
    config_path: Path,
    backends: list[EngineBackend],
) -> str:
    default_engine = override or config.get("default_engine") or "codex"
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
    config: dict,
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
            engine_cfg = get_engine_config(config, engine_id, config_path)
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
        logger.warning("[setup] %s", warning)

    return AutoRouter(entries=entries, default_engine=default_engine)


def _parse_bridge_config(
    *,
    final_notify: bool,
    default_engine_override: str | None,
) -> BridgeConfig:
    startup_pwd = os.getcwd()

    config, config_path = load_telegram_config()
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
    chat_id = chat_id_value

    backends = list_backends()
    default_engine = _resolve_default_engine(
        override=default_engine_override,
        config=config,
        config_path=config_path,
        backends=backends,
    )
    router = _build_router(
        config=config,
        config_path=config_path,
        backends=backends,
        default_engine=default_engine,
    )
    available_engines = [entry.engine for entry in router.available_entries]
    missing_engines = [entry.engine for entry in router.entries if not entry.available]
    engine_list = ", ".join(available_engines) if available_engines else "none"
    if missing_engines:
        engine_list = f"{engine_list} (not installed: {', '.join(missing_engines)})"
    startup_msg = (
        f"\N{OCTOPUS} **takopi is ready**\n\n"
        f"default: `{router.default_engine}`  \n"
        f"agents: `{engine_list}`  \n"
        f"working in: `{startup_pwd}`"
    )

    bot = TelegramClient(token)

    return BridgeConfig(
        bot=bot,
        router=router,
        chat_id=chat_id,
        final_notify=final_notify,
        startup_msg=startup_msg,
    )


def _run_auto_router(
    *, default_engine_override: str | None, final_notify: bool, debug: bool
) -> None:
    setup_logging(debug=debug)
    try:
        default_engine = _default_engine_for_setup(default_engine_override)
        backend = get_backend(default_engine)
    except ConfigError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1)
    setup = check_setup(backend)
    if not setup.ok:
        render_setup_guide(setup)
        raise typer.Exit(code=1)
    try:
        cfg = _parse_bridge_config(
            final_notify=final_notify,
            default_engine_override=default_engine_override,
        )
    except ConfigError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1)
    anyio.run(run_main_loop, cfg)


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
        )
        raise typer.Exit()


def make_engine_cmd(engine_id: str) -> Callable[..., None]:
    def _cmd(
        final_notify: bool = typer.Option(
            True,
            "--final-notify/--no-final-notify",
            help="Send the final response as a new message (not an edit).",
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
