from __future__ import annotations

import os
from pathlib import Path
from typing import cast

import anyio

from ..backends import EngineBackend
from ..runner_bridge import ExecBridgeConfig
from ..logging import get_logger

from ..transports import SetupResult, TransportBackend
from ..transport_runtime import TransportRuntime
from .bridge import (
    TelegramBridgeConfig,
    TelegramPresenter,
    TelegramTransport,
    TelegramFilesConfig,
    TelegramTopicsConfig,
    run_main_loop,
)
from .client import TelegramClient
from .onboarding import check_setup, interactive_setup

logger = get_logger(__name__)


def _build_startup_message(
    runtime: TransportRuntime,
    *,
    startup_pwd: str,
) -> str:
    available_engines = list(runtime.available_engine_ids())
    missing_engines = list(runtime.missing_engine_ids())
    engine_list = ", ".join(available_engines) if available_engines else "none"
    if missing_engines:
        engine_list = f"{engine_list} (not installed: {', '.join(missing_engines)})"
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


def _build_topics_config(transport_config: dict[str, object]) -> TelegramTopicsConfig:
    raw = cast(dict[str, object], transport_config.get("topics", {}))
    return TelegramTopicsConfig(
        enabled=cast(bool, raw.get("enabled", False)),
        scope=cast(str, raw.get("scope", "auto")),
    )


def _build_files_config(transport_config: dict[str, object]) -> TelegramFilesConfig:
    defaults = TelegramFilesConfig()
    raw = cast(dict[str, object], transport_config.get("files", {}))
    return TelegramFilesConfig(
        enabled=cast(bool, raw.get("enabled", defaults.enabled)),
        auto_put=cast(bool, raw.get("auto_put", defaults.auto_put)),
        uploads_dir=cast(str, raw.get("uploads_dir", defaults.uploads_dir)),
        max_upload_bytes=defaults.max_upload_bytes,
        max_download_bytes=defaults.max_download_bytes,
        allowed_user_ids=frozenset(
            cast(
                list[int], raw.get("allowed_user_ids", list(defaults.allowed_user_ids))
            )
        ),
        deny_globs=tuple(
            cast(list[str], raw.get("deny_globs", list(defaults.deny_globs)))
        ),
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

    def lock_token(
        self, *, transport_config: dict[str, object], config_path: Path
    ) -> str | None:
        _ = config_path
        return cast(str, transport_config.get("bot_token"))

    def build_and_run(
        self,
        *,
        transport_config: dict[str, object],
        config_path: Path,
        runtime: TransportRuntime,
        final_notify: bool,
        default_engine_override: str | None,
    ) -> None:
        token = cast(str, transport_config.get("bot_token"))
        chat_id = cast(int, transport_config.get("chat_id"))
        startup_msg = _build_startup_message(
            runtime,
            startup_pwd=os.getcwd(),
        )
        bot = TelegramClient(token)
        transport = TelegramTransport(bot)
        presenter = TelegramPresenter()
        exec_cfg = ExecBridgeConfig(
            transport=transport,
            presenter=presenter,
            final_notify=final_notify,
        )
        topics = _build_topics_config(transport_config)
        files = _build_files_config(transport_config)
        cfg = TelegramBridgeConfig(
            bot=bot,
            runtime=runtime,
            chat_id=chat_id,
            startup_msg=startup_msg,
            exec_cfg=exec_cfg,
            voice_transcription=cast(
                bool, transport_config.get("voice_transcription", False)
            ),
            topics=topics,
            files=files,
        )

        async def run_loop() -> None:
            await run_main_loop(
                cfg,
                watch_config=runtime.watch_config,
                default_engine_override=default_engine_override,
                transport_id=self.id,
                transport_config=transport_config,
            )

        anyio.run(run_loop)


telegram_backend = TelegramBackend()
BACKEND = telegram_backend
