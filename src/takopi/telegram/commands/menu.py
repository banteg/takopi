from __future__ import annotations

from typing import TYPE_CHECKING

from ...commands import get_command
from ...config import ConfigError
from ...ids import RESERVED_COMMAND_IDS, is_valid_id
from ...logging import get_logger
from ...plugins import COMMAND_GROUP, list_entrypoints
from ...transport_runtime import TransportRuntime

if TYPE_CHECKING:
    from ..bridge import TelegramBridgeConfig

logger = get_logger(__name__)

_MAX_BOT_COMMANDS = 100


def build_bot_commands(
    runtime: TransportRuntime,
    *,
    include_file: bool = True,
    include_topics: bool = False,
) -> list[dict[str, str]]:
    commands: list[dict[str, str]] = []
    seen: set[str] = set()
    for engine_id in runtime.available_engine_ids():
        cmd = engine_id.lower()
        if cmd in seen:
            continue
        commands.append({"command": cmd, "description": f"use engine: {cmd}"})
        seen.add(cmd)
    for alias in runtime.project_aliases():
        cmd = alias.lower()
        if cmd in seen:
            continue
        if not is_valid_id(cmd):
            logger.debug(
                "startup.command_menu.skip_project",
                alias=alias,
            )
            continue
        commands.append({"command": cmd, "description": f"work on: {cmd}"})
        seen.add(cmd)
    allowlist = runtime.allowlist
    for ep in list_entrypoints(
        COMMAND_GROUP,
        allowlist=allowlist,
        reserved_ids=RESERVED_COMMAND_IDS,
    ):
        try:
            backend = get_command(ep.name, allowlist=allowlist)
        except ConfigError as exc:
            logger.info(
                "startup.command_menu.skip_command",
                command=ep.name,
                error=str(exc),
            )
            continue
        cmd = backend.id.lower()
        if cmd in seen:
            continue
        if not is_valid_id(cmd):
            logger.debug(
                "startup.command_menu.skip_command_id",
                command=cmd,
            )
            continue
        description = backend.description or f"command: {cmd}"
        commands.append({"command": cmd, "description": description})
        seen.add(cmd)
    for cmd, description in [
        ("new", "start a new thread"),
        ("ctx", "show or update context"),
        ("agent", "set default engine"),
        ("model", "set model override"),
        ("reasoning", "set reasoning override"),
        ("trigger", "set trigger mode"),
    ]:
        if cmd in seen:
            continue
        commands.append({"command": cmd, "description": description})
        seen.add(cmd)
    if include_topics:
        for cmd, description in [("topic", "create or bind a topic")]:
            if cmd in seen:
                continue
            commands.append({"command": cmd, "description": description})
            seen.add(cmd)
    if include_file and "file" not in seen:
        commands.append({"command": "file", "description": "upload or fetch files"})
        seen.add("file")
    if "cancel" not in seen:
        commands.append({"command": "cancel", "description": "cancel run"})
    if len(commands) > _MAX_BOT_COMMANDS:
        logger.warning(
            "startup.command_menu.too_many",
            count=len(commands),
            limit=_MAX_BOT_COMMANDS,
        )
        commands = commands[:_MAX_BOT_COMMANDS]
        if not any(cmd["command"] == "cancel" for cmd in commands):
            commands[-1] = {"command": "cancel", "description": "cancel run"}
    return commands


def _reserved_commands(runtime: TransportRuntime) -> set[str]:
    return {
        *{engine.lower() for engine in runtime.engine_ids},
        *{alias.lower() for alias in runtime.project_aliases()},
        *RESERVED_COMMAND_IDS,
    }


async def _set_command_menu(cfg: TelegramBridgeConfig) -> None:
    commands = build_bot_commands(
        cfg.runtime,
        include_file=cfg.files.enabled,
        include_topics=cfg.topics.enabled,
    )
    if not commands:
        return
    try:
        ok = await cfg.bot.set_my_commands(commands)
    except Exception as exc:  # noqa: BLE001
        logger.info(
            "startup.command_menu.failed",
            error=str(exc),
            error_type=exc.__class__.__name__,
        )
        return
    if not ok:
        logger.info("startup.command_menu.rejected")
        return
    logger.info(
        "startup.command_menu.updated",
        commands=[cmd["command"] for cmd in commands],
    )
