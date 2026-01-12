from __future__ import annotations

from typing import TYPE_CHECKING

from ...logging import get_logger
from ...runner_bridge import RunningTasks
from ...transport import MessageRef
from ..types import TelegramCallbackQuery, TelegramIncomingMessage
from .reply import make_reply

if TYPE_CHECKING:
    from ..bridge import TelegramBridgeConfig

logger = get_logger(__name__)


async def handle_cancel(
    cfg: TelegramBridgeConfig,
    msg: TelegramIncomingMessage,
    running_tasks: RunningTasks,
) -> None:
    reply = make_reply(cfg, msg)
    chat_id = msg.chat_id
    reply_id = msg.reply_to_message_id

    if reply_id is None:
        if msg.reply_to_text:
            await reply(text="nothing is currently running for that message.")
            return
        await reply(text="reply to the progress message to cancel.")
        return

    progress_ref = MessageRef(channel_id=chat_id, message_id=reply_id)
    running_task = running_tasks.get(progress_ref)
    if running_task is None:
        await reply(text="nothing is currently running for that message.")
        return

    logger.info(
        "cancel.requested",
        chat_id=chat_id,
        progress_message_id=reply_id,
    )
    running_task.cancel_requested.set()


async def handle_callback_cancel(
    cfg: TelegramBridgeConfig,
    query: TelegramCallbackQuery,
    running_tasks: RunningTasks,
) -> None:
    progress_ref = MessageRef(channel_id=query.chat_id, message_id=query.message_id)
    running_task = running_tasks.get(progress_ref)
    if running_task is None:
        await cfg.bot.answer_callback_query(
            callback_query_id=query.callback_query_id,
            text="nothing is currently running for that message.",
        )
        return
    logger.info(
        "cancel.requested",
        chat_id=query.chat_id,
        progress_message_id=query.message_id,
    )
    running_task.cancel_requested.set()
    await cfg.bot.answer_callback_query(
        callback_query_id=query.callback_query_id,
        text="cancelling...",
    )
