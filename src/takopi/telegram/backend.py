from __future__ import annotations

import os
from pathlib import Path

import anyio

from ..backends import EngineBackend
from ..runner_bridge import ExecBridgeConfig
from ..config import ConfigError
from ..logging import get_logger
from pydantic import ValidationError

from ..settings import (
    TelegramTransportSettings,
    TelegramFilesSettings,
    TelegramTopicsSettings,
    load_settings,
)
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


def _build_topics_config(
    transport_config: dict[str, object],
    *,
    config_path: Path,
) -> TelegramTopicsConfig:
    raw = transport_config.get("topics") or {}
    if not isinstance(raw, dict):
        raise ConfigError(
            f"Invalid `transports.telegram.topics` in {config_path}; expected a table."
        )
    try:
        settings = TelegramTopicsSettings.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"Invalid topics config in {config_path}: {exc}") from exc
    return TelegramTopicsConfig(
        enabled=settings.enabled,
        scope=settings.scope,
    )


def _build_files_config(
    transport_config: dict[str, object],
    *,
    config_path: Path,
) -> TelegramFilesConfig:
    raw = transport_config.get("files") or {}
    if not isinstance(raw, dict):
        raise ConfigError(
            f"Invalid `transports.telegram.files` in {config_path}; expected a table."
        )
    try:
        settings = TelegramFilesSettings.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"Invalid files config in {config_path}: {exc}") from exc
    return TelegramFilesConfig(
        enabled=settings.enabled,
        auto_put=settings.auto_put,
        uploads_dir=settings.uploads_dir,
        allowed_user_ids=frozenset(settings.allowed_user_ids),
        deny_globs=tuple(settings.deny_globs),
    )


def _require_transport_config(
    transport_config: dict[str, object],
    *,
    config_path: Path,
) -> TelegramTransportSettings:
    try:
        settings = TelegramTransportSettings.model_validate(transport_config)
    except ValidationError as exc:
        raise ConfigError(
            f"Invalid `transports.telegram` in {config_path}: {exc}"
        ) from exc
    token = settings.bot_token.get_secret_value().strip() if settings.bot_token else ""
    if not token:
        raise ConfigError(f"Missing bot token in {config_path}.")
    chat_id = settings.chat_id
    if chat_id is None:
        raise ConfigError(f"Missing chat_id in {config_path}.")
    if isinstance(chat_id, bool) or not isinstance(chat_id, int):
        raise ConfigError(f"Invalid `chat_id` in {config_path}; expected an integer.")
    return settings


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
        settings = _require_transport_config(
            transport_config,
            config_path=config_path,
        )
        return settings.bot_token.get_secret_value().strip() if settings.bot_token else ""

    def build_and_run(
        self,
        *,
        transport_config: dict[str, object],
        config_path: Path,
        runtime: TransportRuntime,
        final_notify: bool,
        default_engine_override: str | None,
    ) -> None:
        watch_enabled = False
        try:
            settings, _ = load_settings(config_path)
        except ConfigError as exc:
            logger.warning(
                "config.watch.disabled",
                error=str(exc),
            )
        else:
            watch_enabled = settings.watch_config

        transport_settings = _require_transport_config(
            transport_config,
            config_path=config_path,
        )
        token = (
            transport_settings.bot_token.get_secret_value().strip()
            if transport_settings.bot_token
            else ""
        )
        chat_id = transport_settings.chat_id
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
        topics = _build_topics_config(transport_config, config_path=config_path)
        files = _build_files_config(transport_config, config_path=config_path)
        cfg = TelegramBridgeConfig(
            bot=bot,
            runtime=runtime,
            chat_id=chat_id,
            startup_msg=startup_msg,
            exec_cfg=exec_cfg,
            voice_transcription=transport_settings.voice_transcription,
            topics=topics,
            files=files,
        )

        async def run_loop() -> None:
            await run_main_loop(
                cfg,
                watch_config=watch_enabled,
                default_engine_override=default_engine_override,
                transport_id=self.id,
                transport_config=transport_config,
            )

        anyio.run(run_loop)


telegram_backend = TelegramBackend()
BACKEND = telegram_backend
