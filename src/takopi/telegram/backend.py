from __future__ import annotations

import os
from pathlib import Path

import anyio

from ..backends import EngineBackend
from ..config import ProjectsConfig
from ..router import AutoRouter
from ..runner_bridge import ExecBridgeConfig
from ..settings import TakopiSettings, require_telegram
from ..transports import SetupResult, TransportBackend
from .bridge import (
    TelegramBridgeConfig,
    TelegramPresenter,
    TelegramTransport,
    run_main_loop,
)
from .client import TelegramClient
from .onboarding import check_setup, interactive_setup


def _build_startup_message(
    router: AutoRouter,
    projects: ProjectsConfig,
    *,
    startup_pwd: str,
) -> str:
    available_engines = [entry.engine for entry in router.available_entries]
    missing_engines = [entry.engine for entry in router.entries if not entry.available]
    engine_list = ", ".join(available_engines) if available_engines else "none"
    if missing_engines:
        engine_list = f"{engine_list} (not installed: {', '.join(missing_engines)})"
    project_aliases = sorted(
        {project.alias for project in projects.projects.values()},
        key=str.lower,
    )
    project_list = ", ".join(project_aliases) if project_aliases else "none"
    return (
        f"\N{OCTOPUS} **takopi is ready**\n\n"
        f"default: `{router.default_engine}`  \n"
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

    def lock_token(self, *, settings: TakopiSettings, config_path: Path) -> str | None:
        token, _ = require_telegram(settings, config_path)
        return token

    def build_and_run(
        self,
        *,
        settings: TakopiSettings,
        config_path: Path,
        router: AutoRouter,
        projects: ProjectsConfig,
        final_notify: bool,
        default_engine_override: str | None,
    ) -> None:
        token, chat_id = require_telegram(settings, config_path)
        startup_msg = _build_startup_message(
            router,
            projects,
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
        cfg = TelegramBridgeConfig(
            bot=bot,
            router=router,
            chat_id=chat_id,
            startup_msg=startup_msg,
            exec_cfg=exec_cfg,
            projects=projects,
        )
        anyio.run(run_main_loop, cfg)


telegram_backend = TelegramBackend()
