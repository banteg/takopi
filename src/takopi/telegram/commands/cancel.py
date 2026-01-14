from __future__ import annotations

from typing import TYPE_CHECKING

from ...logging import get_logger
from ...progress import ProgressTracker
from ...runner_bridge import RunningTasks
from ...scheduler import ThreadJob, ThreadScheduler
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
    scheduler: ThreadScheduler | None = None,
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
        if scheduler is not None:
            job = await scheduler.cancel_queued(chat_id, reply_id)
            if job is not None:
                logger.info(
                    "cancel.queued",
                    chat_id=chat_id,
                    progress_message_id=reply_id,
                    resume=job.resume_token.value,
                )
                await _edit_cancelled_message(cfg, progress_ref, job)
                return
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
    scheduler: ThreadScheduler | None = None,
) -> None:
    progress_ref = MessageRef(channel_id=query.chat_id, message_id=query.message_id)
    running_task = running_tasks.get(progress_ref)
    if running_task is None:
        if scheduler is not None:
            job = await scheduler.cancel_queued(query.chat_id, query.message_id)
            if job is not None:
                logger.info(
                    "cancel.queued",
                    chat_id=query.chat_id,
                    progress_message_id=query.message_id,
                    resume=job.resume_token.value,
                )
                await _edit_cancelled_message(cfg, progress_ref, job)
                await cfg.bot.answer_callback_query(
                    callback_query_id=query.callback_query_id,
                    text="dropped from queue.",
                )
                return
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


async def _edit_cancelled_message(
    cfg: TelegramBridgeConfig,
    progress_ref: MessageRef,
    job: ThreadJob,
) -> None:
    tracker = ProgressTracker(engine=job.resume_token.engine)
    tracker.set_resume(job.resume_token)
    context_line = cfg.runtime.format_context_line(job.context)
    state = tracker.snapshot(context_line=context_line)
    message = cfg.exec_cfg.presenter.render_progress(
        state,
        elapsed_s=0.0,
        label="`cancelled`",
    )
    await cfg.exec_cfg.transport.edit(ref=progress_ref, message=message)
