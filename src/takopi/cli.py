from __future__ import annotations

import os
from collections.abc import Callable

import anyio
import typer

from . import __version__
from .backends import EngineBackend
from .bridge import BridgeConfig, _run_main_loop
from .config import ConfigError, load_telegram_config
from .engines import get_backend, get_engine_config, list_backends
from .logging import setup_logging
from .onboarding import check_setup, render_engine_choice, render_setup_guide
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
    startup_msg = (
        f"\N{OCTOPUS} **takopi is ready**\n\n"
        f"agent: `{backend.id}`  \n"
        f"working in: `{startup_pwd}`"
    )

    bot = TelegramClient(token)
    runner = backend.build_runner(engine_cfg, config_path)

    return BridgeConfig(
        bot=bot,
        runner=runner,
        chat_id=chat_id,
        final_notify=final_notify,
        startup_msg=startup_msg,
    )


def _run_engine(*, engine: str, final_notify: bool, debug: bool) -> None:
    setup_logging(debug=debug)
    try:
        backend = get_backend(engine)
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
        )
    except ConfigError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1)
    anyio.run(_run_main_loop, cfg)


app = typer.Typer(
    add_completion=False,
    invoke_without_command=True,
    help="Run takopi with an explicit engine subcommand.",
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
) -> None:
    """Takopi CLI."""
    if ctx.invoked_subcommand is None:
        render_engine_choice(list_backends())
        raise typer.Exit(code=1)


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
        _run_engine(engine=engine_id, final_notify=final_notify, debug=debug)

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
