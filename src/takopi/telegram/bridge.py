from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import anyio

from ..runner_bridge import (
    ExecBridgeConfig,
    IncomingMessage,
    RunningTask,
    RunningTasks,
    handle_message,
)
from ..logging import bind_run_context, clear_context, get_logger
from ..markdown import MarkdownFormatter, MarkdownParts
from ..model import EngineId, ResumeToken
from ..progress import ProgressState, ProgressTracker
from ..router import AutoRouter, RunnerUnavailableError
from ..runner import Runner
from ..scheduler import ThreadJob, ThreadScheduler
from ..transport import MessageRef, RenderedMessage, SendOptions, Transport
from .client import BotClient
from .render import prepare_telegram

logger = get_logger(__name__)


def _is_cancel_command(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    command = stripped.split(maxsplit=1)[0]
    return command == "/cancel" or command.startswith("/cancel@")


def _strip_engine_command(
    text: str, *, engine_ids: tuple[EngineId, ...]
) -> tuple[str, EngineId | None]:
    if not text:
        return text, None

    if not engine_ids:
        return text, None

    engine_map = {engine.lower(): engine for engine in engine_ids}
    lines = text.splitlines()
    idx = next((i for i, line in enumerate(lines) if line.strip()), None)
    if idx is None:
        return text, None

    line = lines[idx].lstrip()
    if not line.startswith("/"):
        return text, None

    parts = line.split(maxsplit=1)
    command = parts[0][1:]
    if "@" in command:
        command = command.split("@", 1)[0]
    engine = engine_map.get(command.lower())
    if engine is None:
        return text, None

    remainder = parts[1] if len(parts) > 1 else ""
    if remainder:
        lines[idx] = remainder
    else:
        lines.pop(idx)
    return "\n".join(lines).strip(), engine


def _build_bot_commands(router: AutoRouter) -> list[dict[str, str]]:
    commands: list[dict[str, str]] = []
    seen: set[str] = set()
    for entry in router.available_entries:
        cmd = entry.engine.lower()
        if cmd in seen:
            continue
        commands.append({"command": cmd, "description": f"start {cmd}"})
        seen.add(cmd)
    if "cancel" not in seen:
        commands.append({"command": "cancel", "description": "cancel run"})
    return commands


async def _set_command_menu(cfg: TelegramBridgeConfig) -> None:
    commands = _build_bot_commands(cfg.router)
    if not commands:
        return
    try:
        ok = await cfg.bot.set_my_commands(commands)
    except Exception as exc:
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


class TelegramPresenter:
    def __init__(self, *, formatter: MarkdownFormatter | None = None) -> None:
        self._formatter = formatter or MarkdownFormatter()

    def render_progress(
        self,
        state: ProgressState,
        *,
        elapsed_s: float,
        label: str = "working",
    ) -> RenderedMessage:
        parts = self._formatter.render_progress_parts(
            state, elapsed_s=elapsed_s, label=label
        )
        text, entities = prepare_telegram(parts)
        return RenderedMessage(text=text, extra={"entities": entities})

    def render_final(
        self,
        state: ProgressState,
        *,
        elapsed_s: float,
        status: str,
        answer: str,
    ) -> RenderedMessage:
        parts = self._formatter.render_final_parts(
            state, elapsed_s=elapsed_s, status=status, answer=answer
        )
        text, entities = prepare_telegram(parts)
        return RenderedMessage(text=text, extra={"entities": entities})


def _as_int(value: int | str, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"Telegram {label} must be int")
    return value


class TelegramTransport:
    def __init__(self, bot: BotClient) -> None:
        self._bot = bot

    async def close(self) -> None:
        await self._bot.close()

    async def send(
        self,
        *,
        channel_id: int | str,
        message: RenderedMessage,
        options: SendOptions | None = None,
    ) -> MessageRef | None:
        chat_id = _as_int(channel_id, label="chat_id")
        reply_to_message_id: int | None = None
        replace_message_id: int | None = None
        disable_notification = None
        if options is not None:
            disable_notification = not options.notify
            if options.reply_to is not None:
                reply_to_message_id = _as_int(
                    options.reply_to.message_id, label="reply_to_message_id"
                )
            if options.replace is not None:
                replace_message_id = _as_int(
                    options.replace.message_id, label="replace_message_id"
                )
        entities = message.extra.get("entities")
        parse_mode = message.extra.get("parse_mode")
        sent = await self._bot.send_message(
            chat_id=chat_id,
            text=message.text,
            reply_to_message_id=reply_to_message_id,
            disable_notification=disable_notification,
            entities=entities,
            parse_mode=parse_mode,
            replace_message_id=replace_message_id,
        )
        if sent is None:
            return None
        message_id = sent.get("message_id")
        if message_id is None:
            return None
        return MessageRef(
            channel_id=chat_id,
            message_id=_as_int(message_id, label="message_id"),
            raw=sent,
        )

    async def edit(
        self, *, ref: MessageRef, message: RenderedMessage, wait: bool = True
    ) -> MessageRef | None:
        chat_id = _as_int(ref.channel_id, label="chat_id")
        message_id = _as_int(ref.message_id, label="message_id")
        entities = message.extra.get("entities")
        parse_mode = message.extra.get("parse_mode")
        edited = await self._bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=message.text,
            entities=entities,
            parse_mode=parse_mode,
            wait=wait,
        )
        if edited is None:
            return ref if not wait else None
        message_id = edited.get("message_id", message_id)
        return MessageRef(
            channel_id=chat_id,
            message_id=_as_int(message_id, label="message_id"),
            raw=edited,
        )

    async def delete(self, *, ref: MessageRef) -> bool:
        return await self._bot.delete_message(
            chat_id=_as_int(ref.channel_id, label="chat_id"),
            message_id=_as_int(ref.message_id, label="message_id"),
        )


@dataclass(frozen=True)
class TelegramBridgeConfig:
    bot: BotClient
    router: AutoRouter
    chat_id: int
    startup_msg: str
    exec_cfg: ExecBridgeConfig


async def _send_plain(
    transport: Transport,
    *,
    chat_id: int,
    user_msg_id: int,
    text: str,
    notify: bool = True,
) -> None:
    reply_to = MessageRef(channel_id=chat_id, message_id=user_msg_id)
    await transport.send(
        channel_id=chat_id,
        message=RenderedMessage(text=text),
        options=SendOptions(reply_to=reply_to, notify=notify),
    )


async def _send_startup(cfg: TelegramBridgeConfig) -> None:
    logger.debug("startup.message", text=cfg.startup_msg)
    parts = MarkdownParts(header=cfg.startup_msg)
    text, entities = prepare_telegram(parts)
    message = RenderedMessage(text=text, extra={"entities": entities})
    sent = await cfg.exec_cfg.transport.send(
        channel_id=cfg.chat_id,
        message=message,
    )
    if sent is not None:
        logger.info("startup.sent", chat_id=cfg.chat_id)


async def _drain_backlog(cfg: TelegramBridgeConfig, offset: int | None) -> int | None:
    drained = 0
    while True:
        updates = await cfg.bot.get_updates(
            offset=offset, timeout_s=0, allowed_updates=["message"]
        )
        if updates is None:
            logger.info("startup.backlog.failed")
            return offset
        logger.debug("startup.backlog.updates", updates=updates)
        if not updates:
            if drained:
                logger.info("startup.backlog.drained", count=drained)
            return offset
        offset = updates[-1]["update_id"] + 1
        drained += len(updates)


async def poll_updates(cfg: TelegramBridgeConfig) -> AsyncIterator[dict[str, Any]]:
    offset: int | None = None
    offset = await _drain_backlog(cfg, offset)
    await _send_startup(cfg)

    while True:
        updates = await cfg.bot.get_updates(
            offset=offset, timeout_s=50, allowed_updates=["message"]
        )
        if updates is None:
            logger.info("loop.get_updates.failed")
            await anyio.sleep(2)
            continue
        logger.debug("loop.updates", updates=updates)

        for upd in updates:
            offset = upd["update_id"] + 1
            msg = upd["message"]
            if "text" not in msg:
                continue
            if msg["chat"]["id"] != cfg.chat_id:
                continue
            yield msg


async def _handle_cancel(
    cfg: TelegramBridgeConfig,
    msg: dict[str, Any],
    running_tasks: RunningTasks,
) -> None:
    chat_id = msg["chat"]["id"]
    user_msg_id = msg["message_id"]
    reply = msg.get("reply_to_message")

    if not reply:
        await _send_plain(
            cfg.exec_cfg.transport,
            chat_id=chat_id,
            user_msg_id=user_msg_id,
            text="reply to the progress message to cancel.",
        )
        return

    progress_id = reply.get("message_id")
    if progress_id is None:
        await _send_plain(
            cfg.exec_cfg.transport,
            chat_id=chat_id,
            user_msg_id=user_msg_id,
            text="nothing is currently running for that message.",
        )
        return

    progress_ref = MessageRef(channel_id=chat_id, message_id=progress_id)
    running_task = running_tasks.get(progress_ref)
    if running_task is None:
        await _send_plain(
            cfg.exec_cfg.transport,
            chat_id=chat_id,
            user_msg_id=user_msg_id,
            text="nothing is currently running for that message.",
        )
        return

    logger.info(
        "cancel.requested",
        chat_id=chat_id,
        progress_message_id=progress_id,
    )
    running_task.cancel_requested.set()


async def _wait_for_resume(running_task: RunningTask) -> ResumeToken | None:
    if running_task.resume is not None:
        return running_task.resume
    resume: ResumeToken | None = None

    async with anyio.create_task_group() as tg:

        async def wait_resume() -> None:
            nonlocal resume
            await running_task.resume_ready.wait()
            resume = running_task.resume
            tg.cancel_scope.cancel()

        async def wait_done() -> None:
            await running_task.done.wait()
            tg.cancel_scope.cancel()

        tg.start_soon(wait_resume)
        tg.start_soon(wait_done)

    return resume


async def _send_with_resume(
    cfg: TelegramBridgeConfig,
    enqueue: Callable[[int, int, str, ResumeToken], Awaitable[None]],
    running_task: RunningTask,
    chat_id: int,
    user_msg_id: int,
    text: str,
) -> None:
    resume = await _wait_for_resume(running_task)
    if resume is None:
        await _send_plain(
            cfg.exec_cfg.transport,
            chat_id=chat_id,
            user_msg_id=user_msg_id,
            text="resume token not ready yet; try replying to the final message.",
            notify=False,
        )
        return
    await enqueue(chat_id, user_msg_id, text, resume)


async def _send_runner_unavailable(
    cfg: TelegramBridgeConfig,
    *,
    chat_id: int,
    user_msg_id: int,
    resume_token: ResumeToken | None,
    runner: Runner,
    reason: str,
) -> None:
    tracker = ProgressTracker(engine=runner.engine)
    tracker.set_resume(resume_token)
    state = tracker.snapshot(resume_formatter=runner.format_resume)
    message = cfg.exec_cfg.presenter.render_final(
        state,
        elapsed_s=0.0,
        status="error",
        answer=f"error:\n{reason}",
    )
    reply_to = MessageRef(channel_id=chat_id, message_id=user_msg_id)
    await cfg.exec_cfg.transport.send(
        channel_id=chat_id,
        message=message,
        options=SendOptions(reply_to=reply_to, notify=True),
    )


async def run_main_loop(
    cfg: TelegramBridgeConfig,
    poller: Callable[
        [TelegramBridgeConfig], AsyncIterator[dict[str, Any]]
    ] = poll_updates,
) -> None:
    running_tasks: RunningTasks = {}

    try:
        await _set_command_menu(cfg)
        async with anyio.create_task_group() as tg:

            async def run_job(
                chat_id: int,
                user_msg_id: int,
                text: str,
                resume_token: ResumeToken | None,
                reply_ref: MessageRef | None = None,
                on_thread_known: Callable[[ResumeToken, anyio.Event], Awaitable[None]]
                | None = None,
                engine_override: EngineId | None = None,
            ) -> None:
                try:
                    try:
                        entry = (
                            cfg.router.entry_for_engine(engine_override)
                            if resume_token is None
                            else cfg.router.entry_for(resume_token)
                        )
                    except RunnerUnavailableError as exc:
                        await _send_plain(
                            cfg.exec_cfg.transport,
                            chat_id=chat_id,
                            user_msg_id=user_msg_id,
                            text=f"error:\n{exc}",
                        )
                        return
                    if not entry.available:
                        reason = entry.issue or "engine unavailable"
                        await _send_runner_unavailable(
                            cfg,
                            chat_id=chat_id,
                            user_msg_id=user_msg_id,
                            resume_token=resume_token,
                            runner=entry.runner,
                            reason=reason,
                        )
                        return
                    bind_run_context(
                        chat_id=chat_id,
                        user_msg_id=user_msg_id,
                        engine=entry.runner.engine,
                        resume=resume_token.value if resume_token else None,
                    )
                    incoming = IncomingMessage(
                        channel_id=chat_id,
                        message_id=user_msg_id,
                        text=text,
                        reply_to=reply_ref,
                    )
                    await handle_message(
                        cfg.exec_cfg,
                        runner=entry.runner,
                        incoming=incoming,
                        resume_token=resume_token,
                        strip_resume_line=cfg.router.is_resume_line,
                        running_tasks=running_tasks,
                        on_thread_known=on_thread_known,
                    )
                except Exception as exc:
                    logger.exception(
                        "handle.worker_failed",
                        error=str(exc),
                        error_type=exc.__class__.__name__,
                    )
                finally:
                    clear_context()

            async def run_thread_job(job: ThreadJob) -> None:
                await run_job(
                    job.chat_id,
                    job.user_msg_id,
                    job.text,
                    job.resume_token,
                    None,
                )

            scheduler = ThreadScheduler(task_group=tg, run_job=run_thread_job)

            async for msg in poller(cfg):
                text = msg["text"]
                user_msg_id = msg["message_id"]
                chat_id = msg["chat"]["id"]
                reply_ref = None
                reply_msg = msg.get("reply_to_message")
                if reply_msg:
                    reply_id = reply_msg.get("message_id")
                    if reply_id is not None:
                        reply_ref = MessageRef(channel_id=chat_id, message_id=reply_id)

                if _is_cancel_command(text):
                    tg.start_soon(_handle_cancel, cfg, msg, running_tasks)
                    continue

                text, engine_override = _strip_engine_command(
                    text, engine_ids=cfg.router.engine_ids
                )

                r = msg.get("reply_to_message") or {}
                resume_token = cfg.router.resolve_resume(text, r.get("text"))
                reply_id = r.get("message_id")
                if resume_token is None and reply_id is not None:
                    running_task = running_tasks.get(
                        MessageRef(channel_id=chat_id, message_id=reply_id)
                    )
                    if running_task is not None:
                        tg.start_soon(
                            _send_with_resume,
                            cfg,
                            scheduler.enqueue_resume,
                            running_task,
                            chat_id,
                            user_msg_id,
                            text,
                        )
                        continue

                if resume_token is None:
                    tg.start_soon(
                        run_job,
                        chat_id,
                        user_msg_id,
                        text,
                        None,
                        reply_ref,
                        scheduler.note_thread_known,
                        engine_override,
                    )
                else:
                    await scheduler.enqueue_resume(
                        chat_id, user_msg_id, text, resume_token
                    )
    finally:
        await cfg.exec_cfg.transport.close()
