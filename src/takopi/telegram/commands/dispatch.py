from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

import anyio

from ...commands import CommandContext, get_command
from ...config import ConfigError
from ...logging import get_logger
from ...model import EngineId, ResumeToken
from ...runners.run_options import EngineRunOptions
from ...runner_bridge import RunningTasks
from ...scheduler import ThreadScheduler
from ...transport import MessageRef
from ..files import split_command_args
from ..types import TelegramIncomingMessage
from .executor import _TelegramCommandExecutor

if TYPE_CHECKING:
    from ..bridge import TelegramBridgeConfig

logger = get_logger(__name__)


async def _dispatch_command(
    cfg: TelegramBridgeConfig,
    msg: TelegramIncomingMessage,
    text: str,
    command_id: str,
    args_text: str,
    running_tasks: RunningTasks,
    scheduler: ThreadScheduler,
    on_thread_known: Callable[[ResumeToken, anyio.Event], Awaitable[None]] | None,
    stateful_mode: bool,
    default_engine_override: EngineId | None,
    engine_overrides_resolver: Callable[[EngineId], Awaitable[EngineRunOptions | None]]
    | None,
) -> None:
    allowlist = cfg.runtime.allowlist
    chat_id = msg.chat_id
    user_msg_id = msg.message_id
    reply_ref = (
        MessageRef(
            channel_id=chat_id,
            message_id=msg.reply_to_message_id,
            thread_id=msg.thread_id,
        )
        if msg.reply_to_message_id is not None
        else None
    )
    executor = _TelegramCommandExecutor(
        exec_cfg=cfg.exec_cfg,
        runtime=cfg.runtime,
        running_tasks=running_tasks,
        scheduler=scheduler,
        on_thread_known=on_thread_known,
        engine_overrides_resolver=engine_overrides_resolver,
        chat_id=chat_id,
        user_msg_id=user_msg_id,
        thread_id=msg.thread_id,
        show_resume_line=cfg.show_resume_line,
        stateful_mode=stateful_mode,
        default_engine_override=default_engine_override,
    )
    message_ref = MessageRef(
        channel_id=chat_id,
        message_id=user_msg_id,
        thread_id=msg.thread_id,
        sender_id=msg.sender_id,
        raw=msg.raw,
    )
    try:
        backend = get_command(command_id, allowlist=allowlist, required=False)
    except ConfigError as exc:
        await executor.send(f"error:\n{exc}", reply_to=message_ref, notify=True)
        return
    if backend is None:
        return
    try:
        plugin_config = cfg.runtime.plugin_config(command_id)
    except ConfigError as exc:
        await executor.send(f"error:\n{exc}", reply_to=message_ref, notify=True)
        return
    ctx = CommandContext(
        command=command_id,
        text=text,
        args_text=args_text,
        args=split_command_args(args_text),
        message=message_ref,
        reply_to=reply_ref,
        reply_text=msg.reply_to_text,
        config_path=cfg.runtime.config_path,
        plugin_config=plugin_config,
        runtime=cfg.runtime,
        executor=executor,
    )
    try:
        result = await backend.handle(ctx)
    except Exception as exc:
        logger.exception(
            "command.failed",
            command=command_id,
            error=str(exc),
            error_type=exc.__class__.__name__,
        )
        await executor.send(f"error:\n{exc}", reply_to=message_ref, notify=True)
        return
    if result is not None:
        reply_to = message_ref if result.reply_to is None else result.reply_to
        await executor.send(result.text, reply_to=reply_to, notify=result.notify)
