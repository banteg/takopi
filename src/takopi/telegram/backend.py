from __future__ import annotations

import os
from pathlib import Path
from collections.abc import Iterable

import anyio

from .. import __version__
from ..backends import EngineBackend
from ..ids import RESERVED_COMMAND_IDS, RESERVED_ENGINE_IDS
from ..logging import get_logger
from ..plugins import (
    COMMAND_GROUP,
    ENGINE_GROUP,
    TRANSPORT_GROUP,
    entrypoint_distribution_name,
    list_entrypoints,
)
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


def _format_plugins(runtime: TransportRuntime) -> str:
    allowlist = runtime.allowlist

    def _plugin_name(ep) -> str | None:
        dist = entrypoint_distribution_name(ep)
        if dist:
            return dist
        module = ep.value.split(":", 1)[0]
        if not module:
            return None
        return module.split(".", 1)[0] or None

    def _list_external(
        group: str,
        *,
        reserved_ids: Iterable[str] | None = None,
    ) -> list[str]:
        entrypoints = list_entrypoints(
            group,
            allowlist=allowlist,
            reserved_ids=reserved_ids,
        )
        external: list[str] = []
        seen: set[str] = set()
        for ep in entrypoints:
            name = _plugin_name(ep)
            if not name:
                continue
            lowered = name.lower()
            if lowered == "takopi":
                continue
            if lowered in seen:
                continue
            seen.add(lowered)
            external.append(name)
        return external

    engine_ids = _list_external(ENGINE_GROUP, reserved_ids=RESERVED_ENGINE_IDS)
    transport_ids = _list_external(TRANSPORT_GROUP)
    command_ids = _list_external(COMMAND_GROUP, reserved_ids=RESERVED_COMMAND_IDS)

    parts: list[str] = []
    if engine_ids:
        parts.append(f"engines={', '.join(engine_ids)}")
    if transport_ids:
        parts.append(f"transports={', '.join(transport_ids)}")
    if command_ids:
        parts.append(f"commands={', '.join(command_ids)}")
    if not parts:
        return "none"
    return "; ".join(parts)


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
    project_aliases = sorted(set(runtime.project_aliases()), key=str.lower)
    project_list = ", ".join(project_aliases) if project_aliases else "none"
    plugins_list = _format_plugins(runtime)
    return (
        f"\N{OCTOPUS} **takopi is ready**\n\n"
        f"version: `{__version__}`  \n"
        f"default: `{runtime.default_engine}`  \n"
        f"agents: `{engine_list}`  \n"
        f"projects: `{project_list}`  \n"
        f"plugins: `{plugins_list}`  \n"
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

    def lock_token(self, *, transport_config: object, _config_path: Path) -> str | None:
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
            session_mode=settings.session_mode,
            show_resume_line=settings.show_resume_line,
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
