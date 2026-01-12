from __future__ import annotations

import os
from pathlib import Path

import anyio

from ..backends import EngineBackend
from ..logging import get_logger
from ..runner_bridge import ExecBridgeConfig
from ..settings import TelegramTransportSettings
from ..transport_runtime import TransportRuntime
from ..transports import SetupResult, TransportBackend
from .bridge import (
    TelegramBridgeConfig,
    TelegramPresenter,
    TelegramTransport,
    run_main_loop,
)
from .client import TelegramClient
from .onboarding import check_setup, interactive_setup

logger = get_logger(__name__)


def _expect_transport_settings(transport_config: object) -> TelegramTransportSettings:
    if isinstance(transport_config, TelegramTransportSettings):
        return transport_config
    raise TypeError("transport_config must be TelegramTransportSettings")


def _build_startup_message(
    runtime: TransportRuntime,
    *,
    startup_pwd: str,
) -> str:
    available_engines = list(runtime.available_engine_ids())
    missing_engines = list(runtime.missing_engine_ids())
    misconfigured_engines = list(runtime.engine_ids_with_status("bad_config"))
    failed_engines = list(runtime.engine_ids_with_status("load_error"))

    engine_list = ", ".join(available_engines) if available_engines else "none"

    notes: list[str] = []
    if missing_engines:
        notes.append(f"not installed: {', '.join(missing_engines)}")
    if misconfigured_engines:
        notes.append(f"misconfigured: {', '.join(misconfigured_engines)}")
    if failed_engines:
        notes.append(f"failed to load: {', '.join(failed_engines)}")
    if notes:
        engine_list = f"{engine_list} ({'; '.join(notes)})"
    project_aliases = sorted(
        {alias for alias in runtime.project_aliases()}, key=str.lower
    )
    project_list = ", ".join(project_aliases) if project_aliases else "none"
    return (
        f"\N{OCTOPUS} **takopi is ready**\n\n"
        f"default: `{runtime.default_engine}`  \n"
        f"agents: `{engine_list}`  \n"
        f"projects: `{project_list}`  \n"
        f"working in: `{startup_pwd}`"
    )


class TelegramBackend(TransportBackend):
    id = "telegram"
    description = "Telegram bot"

    def check_setup(
        self,
        engine_backend: EngineBackend,
        *,
        transport_override: str | None = None,
    ) -> SetupResult:
        return check_setup(engine_backend, transport_override=transport_override)

    def interactive_setup(self, *, force: bool) -> bool:
        return interactive_setup(force=force)

    def lock_token(self, *, transport_config: object, config_path: Path) -> str | None:
        _ = config_path
        settings = _expect_transport_settings(transport_config)
        return settings.bot_token

    def build_and_run(
        self,
        *,
        transport_config: object,
        config_path: Path,
        runtime: TransportRuntime,
        final_notify: bool,
        default_engine_override: str | None,
    ) -> None:
        settings = _expect_transport_settings(transport_config)
        token = settings.bot_token
        chat_id = settings.chat_id
        startup_msg = _build_startup_message(
            runtime,
            startup_pwd=os.getcwd(),
        )
        bot = TelegramClient(token)
        transport = TelegramTransport(bot)
        presenter = TelegramPresenter(message_overflow=settings.message_overflow)
        exec_cfg = ExecBridgeConfig(
            transport=transport,
            presenter=presenter,
            final_notify=final_notify,
        )
        cfg = TelegramBridgeConfig(
            bot=bot,
            runtime=runtime,
            chat_id=chat_id,
            startup_msg=startup_msg,
            exec_cfg=exec_cfg,
            voice_transcription=settings.voice_transcription,
            voice_max_bytes=int(settings.voice_max_bytes),
            voice_transcription_model=settings.voice_transcription_model,
            topics=settings.topics,
            files=settings.files,
        )

        async def run_loop() -> None:
            await run_main_loop(
                cfg,
                watch_config=runtime.watch_config,
                default_engine_override=default_engine_override,
                transport_id=self.id,
                transport_config=settings,
            )

        anyio.run(run_loop)


telegram_backend = TelegramBackend()
BACKEND = telegram_backend
