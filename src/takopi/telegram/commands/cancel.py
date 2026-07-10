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


async def handle_callback_steer(
    cfg: TelegramBridgeConfig,
    query: TelegramCallbackQuery,
    running_tasks: RunningTasks,
    scheduler: ThreadScheduler | None = None,
) -> None:
    if scheduler is None:
        await cfg.bot.answer_callback_query(
            callback_query_id=query.callback_query_id,
            text="no queue is available.",
        )
        return

    progress_ref = MessageRef(channel_id=query.chat_id, message_id=query.message_id)
    job = await scheduler.get_queued(query.chat_id, query.message_id)
    if job is None:
        await cfg.bot.answer_callback_query(
            callback_query_id=query.callback_query_id,
            text="this message is not queued.",
        )
        return

    control = None
    for running_task in running_tasks.values():
        if running_task.resume == job.resume_token:
            control = running_task.control
            break
    if control is None:
        await cfg.bot.answer_callback_query(
            callback_query_id=query.callback_query_id,
            text="active turn is not steerable; still queued.",
        )
        return

    claimed = await scheduler.claim_queued(query.chat_id, query.message_id)
    if claimed is None:
        await cfg.bot.answer_callback_query(
            callback_query_id=query.callback_query_id,
            text="already left the queue.",
        )
        return

    try:
        await control.steer(claimed.text)
    except Exception as exc:  # noqa: BLE001
        await scheduler.requeue_front(claimed)
        logger.warning(
            "steer.failed",
            chat_id=query.chat_id,
            progress_message_id=query.message_id,
            resume=claimed.resume_token.value,
            error=str(exc),
            error_type=exc.__class__.__name__,
        )
        await cfg.bot.answer_callback_query(
            callback_query_id=query.callback_query_id,
            text="could not steer; still queued.",
        )
        return

    await _edit_labelled_message(cfg, progress_ref, claimed, label="steered")
    await cfg.bot.answer_callback_query(
        callback_query_id=query.callback_query_id,
        text="steered active turn.",
    )


async def _edit_cancelled_message(
    cfg: TelegramBridgeConfig,
    progress_ref: MessageRef,
    job: ThreadJob,
) -> None:
    await _edit_labelled_message(cfg, progress_ref, job, label="cancelled")


async def _edit_labelled_message(
    cfg: TelegramBridgeConfig,
    progress_ref: MessageRef,
    job: ThreadJob,
    *,
    label: str,
) -> None:
    tracker = ProgressTracker(engine=job.resume_token.engine)
    tracker.set_resume(job.resume_token)
    context_line = cfg.runtime.format_context_line(job.context)
    resume_formatter = None
    if cfg.show_resume_line or cfg.session_mode != "chat":
        resume_formatter = cfg.runtime.resolve_runner(
            resume_token=job.resume_token,
            engine_override=None,
        ).runner.format_resume
    state = tracker.snapshot(
        resume_formatter=resume_formatter,
        context_line=context_line,
    )
    message = cfg.exec_cfg.presenter.render_progress(
        state,
        elapsed_s=0.0,
        label=label,
    )
    await cfg.exec_cfg.transport.edit(ref=progress_ref, message=message)
