from __future__ import annotations

import os
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import anyio
import typer

from ..config import ConfigError
from ..engines import list_backend_ids
from ..ids import RESERVED_CHAT_COMMANDS
from ..runtime_loader import resolve_plugins_allowlist
from ..settings import TakopiSettings, TelegramTopicsSettings
from ..telegram.client import TelegramClient
from ..telegram.topics import _validate_topics_setup_for

DoctorStatus = Literal["ok", "warning", "error"]


@dataclass(frozen=True, slots=True)
class DoctorCheck:
    label: str
    status: DoctorStatus
    detail: str | None = None

    def render(self) -> str:
        if self.detail:
            return f"- {self.label}: {self.status} ({self.detail})"
        return f"- {self.label}: {self.status}"


def _doctor_file_checks(settings: TakopiSettings) -> list[DoctorCheck]:
    files = settings.transports.telegram.files
    if not files.enabled:
        return [DoctorCheck("file transfer", "ok", "disabled")]
    if files.allowed_user_ids:
        count = len(files.allowed_user_ids)
        detail = f"restricted to {count} user id(s)"
        return [DoctorCheck("file transfer", "ok", detail)]
    return [DoctorCheck("file transfer", "warning", "enabled for all users")]


def _doctor_voice_checks(settings: TakopiSettings) -> list[DoctorCheck]:
    if not settings.transports.telegram.voice_transcription:
        return [DoctorCheck("voice transcription", "ok", "disabled")]
    api_key = settings.transports.telegram.voice_transcription_api_key
    if api_key:
        return [
            DoctorCheck("voice transcription", "ok", "voice_transcription_api_key set")
        ]
    if os.environ.get("OPENAI_API_KEY"):
        return [DoctorCheck("voice transcription", "ok", "OPENAI_API_KEY set")]
    return [DoctorCheck("voice transcription", "error", "API key not set")]


async def _doctor_telegram_checks(
    token: str,
    chat_id: int,
    topics: TelegramTopicsSettings,
    project_chat_ids: tuple[int, ...],
) -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []
    client_factory = _resolve_cli_attr("TelegramClient") or TelegramClient
    validate_topics = (
        _resolve_cli_attr("_validate_topics_setup_for") or _validate_topics_setup_for
    )
    bot = client_factory(token)
    try:
        me = await bot.get_me()
        if me is None:
            checks.append(
                DoctorCheck("telegram token", "error", "failed to fetch bot info")
            )
            checks.append(DoctorCheck("chat_id", "error", "skipped (token invalid)"))
            if topics.enabled:
                checks.append(DoctorCheck("topics", "error", "skipped (token invalid)"))
            else:
                checks.append(DoctorCheck("topics", "ok", "disabled"))
            return checks
        bot_label = f"@{me.username}" if me.username else f"id={me.id}"
        checks.append(DoctorCheck("telegram token", "ok", bot_label))
        chat = await bot.get_chat(chat_id)
        if chat is None:
            checks.append(DoctorCheck("chat_id", "error", f"unreachable ({chat_id})"))
        else:
            checks.append(DoctorCheck("chat_id", "ok", f"{chat.type} ({chat_id})"))
        if topics.enabled:
            try:
                await validate_topics(
                    bot=bot,
                    topics=topics,
                    chat_id=chat_id,
                    project_chat_ids=project_chat_ids,
                )
                checks.append(DoctorCheck("topics", "ok", f"scope={topics.scope}"))
            except ConfigError as exc:
                checks.append(DoctorCheck("topics", "error", str(exc)))
        else:
            checks.append(DoctorCheck("topics", "ok", "disabled"))
    except Exception as exc:  # noqa: BLE001
        checks.append(DoctorCheck("telegram", "error", str(exc)))
    finally:
        await bot.close()
    return checks


def run_doctor(
    *,
    load_settings_fn: Callable[[], tuple[TakopiSettings, Path]],
    telegram_checks: Callable[
        [str, int, TelegramTopicsSettings, tuple[int, ...]],
        Awaitable[list[DoctorCheck]],
    ],
    file_checks: Callable[[TakopiSettings], list[DoctorCheck]],
    voice_checks: Callable[[TakopiSettings], list[DoctorCheck]],
) -> None:
    try:
        settings, config_path = load_settings_fn()
    except ConfigError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    if settings.transport != "telegram":
        typer.echo(
            "error: takopi doctor currently supports the telegram transport only.",
            err=True,
        )
        raise typer.Exit(code=1)

    allowlist = resolve_plugins_allowlist(settings)
    engine_ids = list_backend_ids(allowlist=allowlist)
    try:
        projects_cfg = settings.to_projects_config(
            config_path=config_path,
            engine_ids=engine_ids,
            reserved=RESERVED_CHAT_COMMANDS,
        )
    except ConfigError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    tg = settings.transports.telegram
    project_chat_ids = projects_cfg.project_chat_ids()
    telegram_checks_result = anyio.run(
        telegram_checks,
        tg.bot_token,
        tg.chat_id,
        tg.topics,
        project_chat_ids,
    )
    if telegram_checks_result is None:
        telegram_checks_result = []
    checks = [
        *telegram_checks_result,
        *file_checks(settings),
        *voice_checks(settings),
    ]
    typer.echo("takopi doctor")
    for check in checks:
        typer.echo(check.render())
    if any(check.status == "error" for check in checks):
        raise typer.Exit(code=1)


def _resolve_cli_attr(name: str) -> object | None:
    cli_module = sys.modules.get("takopi.cli")
    if cli_module is None:
        return None
    return getattr(cli_module, name, None)
