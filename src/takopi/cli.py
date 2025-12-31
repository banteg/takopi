from __future__ import annotations

import os
from typing import Any

import anyio
import typer

from . import __version__
from .bridge import BridgeConfig, _run_main_loop
from .config import ConfigError, load_telegram_config
from .engines import (
    EngineBackend,
    get_backend,
    get_engine_config,
    list_backend_ids,
    parse_engine_overrides,
)
from .logging import setup_logging
from .onboarding import check_setup, render_setup_guide
from .telegram import TelegramClient


def _print_version_and_exit() -> None:
    typer.echo(__version__)
    raise typer.Exit()


def _version_callback(value: bool) -> None:
    if value:
        _print_version_and_exit()


def _parse_bridge_config(
    *,
    final_notify: bool,
    backend: EngineBackend,
    engine_overrides: dict[str, Any],
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

    engine_cfg = get_engine_config(config, backend.id, config_path)
    startup_msg = backend.startup_message(startup_pwd)

    bot = TelegramClient(token)
    runner = backend.build_runner(engine_cfg, engine_overrides, config_path)

    return BridgeConfig(
        bot=bot,
        runner=runner,
        chat_id=chat_id,
        final_notify=final_notify,
        startup_msg=startup_msg,
    )


def run(
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
    engine: str = typer.Option(
        "codex",
        "--engine",
        help=f"Engine backend id ({', '.join(list_backend_ids())}).",
    ),
    debug: bool = typer.Option(
        False,
        "--debug/--no-debug",
        help="Log engine JSONL, Telegram requests, and rendered messages.",
    ),
    engine_option: list[str] = typer.Option(
        [],
        "--engine-option",
        "-E",
        help="Engine-specific override in KEY=VALUE form (repeatable).",
    ),
) -> None:
    setup_logging(debug=debug)
    try:
        backend = get_backend(engine)
    except ConfigError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1)
    try:
        overrides = parse_engine_overrides(engine_option)
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
            backend=backend,
            engine_overrides=overrides,
        )
    except ConfigError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1)
    anyio.run(_run_main_loop, cfg)


def main() -> None:
    typer.run(run)


if __name__ == "__main__":
    main()
