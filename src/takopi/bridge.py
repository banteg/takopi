"""Telegram bridge orchestration for running a single runner and streaming progress."""

from __future__ import annotations

import logging
import time
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import anyio

from .markdown import TELEGRAM_MARKDOWN_LIMIT, prepare_telegram
from .model import CompletedEvent, ResumeToken, StartedEvent, TakopiEvent
from .render import ExecProgressRenderer, render_event_cli
from .runner import Runner
from .telegram import BotClient


logger = logging.getLogger(__name__)


def _resolve_resume(
    runner: Runner, text: str | None, reply_text: str | None
) -> ResumeToken | None:
    return runner.extract_resume(text) or runner.extract_resume(reply_text)


def _log_runner_event(evt: TakopiEvent) -> None:
    for line in render_event_cli(evt):
        logger.info("[runner] %s", line)
    if isinstance(evt, CompletedEvent):
        if evt.ok:
            logger.info("[runner] done")
        else:
            logger.info("[runner] error: %s", evt.error or "error")


def _is_cancel_command(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    command = stripped.split(maxsplit=1)[0]
    return command == "/cancel" or command.startswith("/cancel@")


def _strip_resume_lines(text: str, *, is_resume_line: Callable[[str], bool]) -> str:
    stripped_lines: list[str] = []
    for line in text.splitlines():
        if is_resume_line(line):
            continue
        stripped_lines.append(line)
    prompt = "\n".join(stripped_lines).strip()
    return prompt or "continue"


PROGRESS_EDIT_EVERY_S = 2.0


async def _send_or_edit_markdown(
    bot: BotClient,
    *,
    chat_id: int,
    text: str,
    edit_message_id: int | None = None,
    reply_to_message_id: int | None = None,
    disable_notification: bool = False,
    limit: int = TELEGRAM_MARKDOWN_LIMIT,
    is_resume_line: Callable[[str], bool] | None = None,
    prepared: tuple[str, list[dict[str, Any]] | None] | None = None,
) -> tuple[dict[str, Any] | None, bool]:
    if prepared is None:
        rendered, entities = prepare_telegram(
            text, limit=limit, is_resume_line=is_resume_line
        )
    else:
        rendered, entities = prepared
    if edit_message_id is not None:
        edited = await bot.edit_message_text(
            chat_id=chat_id,
            message_id=edit_message_id,
            text=rendered,
            entities=entities,
        )
        if edited is not None:
            return (edited, True)

    return (
        await bot.send_message(
            chat_id=chat_id,
            text=rendered,
            entities=entities,
            reply_to_message_id=reply_to_message_id,
            disable_notification=disable_notification,
        ),
        False,
    )


class ProgressEdits:
    def __init__(
        self,
        *,
        bot: BotClient,
        chat_id: int,
        progress_id: int | None,
        renderer: ExecProgressRenderer,
        started_at: float,
        progress_edit_every: float,
        clock: Callable[[], float],
        sleep: Callable[[float], Awaitable[None]],
        limit: int,
        last_edit_at: float,
        last_rendered: str | None,
        is_resume_line: Callable[[str], bool],
    ) -> None:
        self.bot = bot
        self.chat_id = chat_id
        self.progress_id = progress_id
        self.renderer = renderer
        self.started_at = started_at
        self.progress_edit_every = progress_edit_every
        self.clock = clock
        self.sleep = sleep
        self.limit = limit
        self.last_edit_at = last_edit_at
        self.last_rendered = last_rendered
        self.is_resume_line = is_resume_line
        self._event_seq = 0
        self._published_seq = 0
        self.wakeup = anyio.Event()

    async def _wait_for_wakeup(self) -> None:
        await self.wakeup.wait()
        self.wakeup = anyio.Event()

    async def run(self) -> None:
        if self.progress_id is None:
            return
        while True:
            await self._wait_for_wakeup()
            while self._published_seq < self._event_seq:
                await self.sleep(
                    max(
                        0.0,
                        self.last_edit_at + self.progress_edit_every - self.clock(),
                    )
                )

                seq_at_render = self._event_seq
                now = self.clock()
                md = self.renderer.render_progress(now - self.started_at)
                rendered, entities = prepare_telegram(
                    md, limit=self.limit, is_resume_line=self.is_resume_line
                )
                if rendered != self.last_rendered:
                    logger.debug(
                        "[progress] edit message_id=%s md=%s", self.progress_id, md
                    )
                    self.last_edit_at = now
                    edited = await self.bot.edit_message_text(
                        chat_id=self.chat_id,
                        message_id=self.progress_id,
                        text=rendered,
                        entities=entities,
                    )
                    if edited is not None:
                        self.last_rendered = rendered

                self._published_seq = seq_at_render

    async def on_event(self, evt: TakopiEvent) -> None:
        if not self.renderer.note_event(evt):
            return
        if self.progress_id is None:
            return
        self._event_seq += 1
        self.wakeup.set()


@dataclass(frozen=True)
class BridgeConfig:
    bot: BotClient
    runner: Runner
    chat_id: int
    final_notify: bool
    startup_msg: str
    progress_edit_every: float = PROGRESS_EDIT_EVERY_S


@dataclass
class RunningTask:
    resume: ResumeToken | None = None
    resume_ready: anyio.Event = field(default_factory=anyio.Event)
    cancel_requested: anyio.Event = field(default_factory=anyio.Event)
    done: anyio.Event = field(default_factory=anyio.Event)


async def _send_startup(cfg: BridgeConfig) -> None:
    logger.debug("[startup] message: %s", cfg.startup_msg)
    sent, _ = await _send_or_edit_markdown(
        cfg.bot,
        chat_id=cfg.chat_id,
        text=cfg.startup_msg,
        limit=TELEGRAM_MARKDOWN_LIMIT,
    )
    if sent is not None:
        logger.info("[startup] sent startup message to chat_id=%s", cfg.chat_id)


async def _drain_backlog(cfg: BridgeConfig, offset: int | None) -> int | None:
    drained = 0
    while True:
        updates = await cfg.bot.get_updates(
            offset=offset, timeout_s=0, allowed_updates=["message"]
        )
        if updates is None:
            logger.info("[startup] backlog drain failed")
            return offset
        logger.debug("[startup] backlog updates: %s", updates)
        if not updates:
            if drained:
                logger.info("[startup] drained %s pending update(s)", drained)
            return offset
        offset = updates[-1]["update_id"] + 1
        drained += len(updates)


async def handle_message(
    cfg: BridgeConfig,
    *,
    chat_id: int,
    user_msg_id: int,
    text: str,
    resume_token: ResumeToken | None,
    running_tasks: dict[int, RunningTask] | None = None,
    on_thread_known: Callable[[ResumeToken, anyio.Event], Awaitable[None]]
    | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[None]] = anyio.sleep,
    progress_edit_every: float = PROGRESS_EDIT_EVERY_S,
) -> None:
    logger.debug(
        "[handle] incoming chat_id=%s message_id=%s resume=%r text=%s",
        chat_id,
        user_msg_id,
        resume_token,
        text,
    )
    started_at = clock()
    runner = cfg.runner
    is_resume_line = runner.is_resume_line
    runner_text = _strip_resume_lines(text, is_resume_line=is_resume_line)

    progress_renderer = ExecProgressRenderer(
        max_actions=5, resume_formatter=runner.format_resume
    )

    progress_id: int | None = None
    last_edit_at = 0.0
    last_rendered: str | None = None

    initial_md = progress_renderer.render_progress(
        0.0, label=f"working ({runner.engine})"
    )
    initial_rendered, initial_entities = prepare_telegram(
        initial_md, limit=TELEGRAM_MARKDOWN_LIMIT, is_resume_line=is_resume_line
    )
    logger.debug(
        "[progress] send reply_to=%s md=%s rendered=%s entities=%s",
        user_msg_id,
        initial_md,
        initial_rendered,
        initial_entities,
    )
    progress_msg = await cfg.bot.send_message(
        chat_id=chat_id,
        text=initial_rendered,
        entities=initial_entities,
        reply_to_message_id=user_msg_id,
        disable_notification=True,
    )
    if progress_msg is not None:
        progress_id = int(progress_msg["message_id"])
        last_edit_at = clock()
        last_rendered = initial_rendered
        logger.debug("[progress] sent chat_id=%s message_id=%s", chat_id, progress_id)

    edits = ProgressEdits(
        bot=cfg.bot,
        chat_id=chat_id,
        progress_id=progress_id,
        renderer=progress_renderer,
        started_at=started_at,
        progress_edit_every=progress_edit_every,
        clock=clock,
        sleep=sleep,
        limit=TELEGRAM_MARKDOWN_LIMIT,
        last_edit_at=last_edit_at,
        last_rendered=last_rendered,
        is_resume_line=is_resume_line,
    )

    cancel_exc_type = anyio.get_cancelled_exc_class()
    cancelled = False
    error: Exception | None = None
    resume_token_value: ResumeToken | None = None
    answer: str | None = None
    run_ok: bool | None = None
    run_error: str | None = None
    running_task: RunningTask | None = None
    if running_tasks is not None and progress_id is not None:
        running_task = RunningTask()
        running_tasks[progress_id] = running_task

    edits_scope = anyio.CancelScope()

    async def run_edits() -> None:
        try:
            with edits_scope:
                await edits.run()
        except cancel_exc_type:
            # Edits are best-effort; cancellation should not bubble into the task group.
            return

    async with anyio.create_task_group() as tg:
        if progress_id is not None:
            tg.start_soon(run_edits)

        async def run_exec() -> CompletedEvent | None:
            nonlocal cancelled
            cancel_flag = False
            completed: CompletedEvent | None = None

            async with anyio.create_task_group() as exec_tg:

                async def run_runner() -> None:
                    nonlocal resume_token_value, completed, answer, run_ok, run_error
                    try:
                        async for evt in runner.run(runner_text, resume_token):
                            _log_runner_event(evt)
                            if isinstance(evt, StartedEvent):
                                resume_token_value = evt.resume
                                if (
                                    running_task is not None
                                    and running_task.resume is None
                                ):
                                    running_task.resume = resume_token_value
                                    running_task.resume_ready.set()
                                    if on_thread_known is not None:
                                        await on_thread_known(
                                            resume_token_value, running_task.done
                                        )
                            elif isinstance(evt, CompletedEvent):
                                resume_token_value = evt.resume or resume_token_value
                                answer = evt.answer
                                run_ok = evt.ok
                                run_error = evt.error
                                completed = evt
                            await edits.on_event(evt)
                    finally:
                        exec_tg.cancel_scope.cancel()

                async def wait_cancel() -> None:
                    nonlocal cancel_flag
                    if running_task is None:
                        return
                    await running_task.cancel_requested.wait()
                    cancel_flag = True
                    exec_tg.cancel_scope.cancel()

                exec_tg.start_soon(run_runner)
                if running_task is not None:
                    exec_tg.start_soon(wait_cancel)

            if cancel_flag:
                cancelled = True
            return completed

        try:
            completed = await run_exec()
            if completed is not None:
                resume_token_value = completed.resume or resume_token_value
                answer = completed.answer
                run_ok = completed.ok
                run_error = completed.error
        except Exception as e:
            error = e
        finally:
            if (
                running_task is not None
                and running_tasks is not None
                and progress_id is not None
            ):
                running_task.done.set()
                running_tasks.pop(progress_id, None)
            if not cancelled and error is None:
                await anyio.sleep(0)
            edits_scope.cancel()

    if error is not None:
        elapsed = clock() - started_at
        if resume_token_value is None:
            resume_token_value = progress_renderer.resume_token
        progress_renderer.resume_token = resume_token_value
        err_body = f"Error:\n{error}"
        final_md = progress_renderer.render_final(elapsed, err_body, status="error")
        logger.debug("[error] markdown: %s", final_md)
        final_msg, edited = await _send_or_edit_markdown(
            cfg.bot,
            chat_id=chat_id,
            text=final_md,
            edit_message_id=progress_id,
            reply_to_message_id=user_msg_id,
            disable_notification=True,
            limit=TELEGRAM_MARKDOWN_LIMIT,
            is_resume_line=is_resume_line,
        )
        if final_msg is None:
            return
        if progress_id is not None and not edited:
            logger.debug("[error] delete progress message_id=%s", progress_id)
            await cfg.bot.delete_message(chat_id=chat_id, message_id=progress_id)
        return

    elapsed = clock() - started_at
    if cancelled:
        if resume_token_value is None:
            resume_token_value = progress_renderer.resume_token
        logger.info(
            "[handle] cancelled resume=%s elapsed=%.1fs",
            resume_token_value.value if resume_token_value else None,
            elapsed,
        )
        progress_renderer.resume_token = resume_token_value
        final_md = progress_renderer.render_progress(elapsed, label="`cancelled`")
        final_msg, edited = await _send_or_edit_markdown(
            cfg.bot,
            chat_id=chat_id,
            text=final_md,
            edit_message_id=progress_id,
            reply_to_message_id=user_msg_id,
            disable_notification=True,
            limit=TELEGRAM_MARKDOWN_LIMIT,
            is_resume_line=is_resume_line,
        )
        if final_msg is None:
            return
        if progress_id is not None and not edited:
            logger.debug("[cancel] delete progress message_id=%s", progress_id)
            await cfg.bot.delete_message(chat_id=chat_id, message_id=progress_id)
        return

    if answer is None:
        raise RuntimeError("runner finished without a completed event")

    final_answer = answer
    if run_ok is False and run_error:
        if final_answer.strip():
            final_answer = f"{final_answer}\n\nError:\n{run_error}"
        else:
            final_answer = f"Error:\n{run_error}"

    status = (
        "error" if run_ok is False else ("done" if final_answer.strip() else "error")
    )
    if resume_token_value is None:
        resume_token_value = progress_renderer.resume_token
    progress_renderer.resume_token = resume_token_value
    final_md = progress_renderer.render_final(elapsed, final_answer, status=status)
    logger.debug("[final] markdown: %s", final_md)
    final_rendered, final_entities = prepare_telegram(
        final_md, limit=TELEGRAM_MARKDOWN_LIMIT, is_resume_line=is_resume_line
    )
    can_edit_final = progress_id is not None and final_entities is not None
    edit_message_id = None if cfg.final_notify or not can_edit_final else progress_id

    if edit_message_id is None:
        logger.debug(
            "[final] send reply_to=%s rendered=%s entities=%s",
            user_msg_id,
            final_rendered,
            final_entities,
        )
    else:
        logger.debug(
            "[final] edit message_id=%s rendered=%s entities=%s",
            edit_message_id,
            final_rendered,
            final_entities,
        )

    final_msg, edited = await _send_or_edit_markdown(
        cfg.bot,
        chat_id=chat_id,
        text=final_md,
        edit_message_id=edit_message_id,
        reply_to_message_id=user_msg_id,
        disable_notification=False,
        limit=TELEGRAM_MARKDOWN_LIMIT,
        is_resume_line=is_resume_line,
        prepared=(final_rendered, final_entities),
    )
    if final_msg is None:
        return
    if progress_id is not None and (edit_message_id is None or not edited):
        logger.debug("[final] delete progress message_id=%s", progress_id)
        await cfg.bot.delete_message(chat_id=chat_id, message_id=progress_id)


async def poll_updates(cfg: BridgeConfig) -> AsyncIterator[dict[str, Any]]:
    offset: int | None = None
    offset = await _drain_backlog(cfg, offset)
    await _send_startup(cfg)

    while True:
        updates = await cfg.bot.get_updates(
            offset=offset, timeout_s=50, allowed_updates=["message"]
        )
        if updates is None:
            logger.info("[loop] getUpdates failed")
            await anyio.sleep(2)
            continue
        logger.debug("[loop] updates: %s", updates)

        for upd in updates:
            offset = upd["update_id"] + 1
            msg = upd["message"]
            if "text" not in msg:
                continue
            if not (msg["chat"]["id"] == msg["from"]["id"] == cfg.chat_id):
                continue
            yield msg


async def _handle_cancel(
    cfg: BridgeConfig,
    msg: dict[str, Any],
    running_tasks: dict[int, RunningTask],
) -> None:
    chat_id = msg["chat"]["id"]
    user_msg_id = msg["message_id"]
    reply = msg.get("reply_to_message")

    if not reply:
        await cfg.bot.send_message(
            chat_id=chat_id,
            text="reply to the progress message to cancel.",
            reply_to_message_id=user_msg_id,
        )
        return

    progress_id = reply.get("message_id")
    if progress_id is None:
        await cfg.bot.send_message(
            chat_id=chat_id,
            text="nothing is currently running for that message.",
            reply_to_message_id=user_msg_id,
        )
        return

    running_task = running_tasks.get(int(progress_id))
    if running_task is None:
        await cfg.bot.send_message(
            chat_id=chat_id,
            text="nothing is currently running for that message.",
            reply_to_message_id=user_msg_id,
        )
        return

    logger.info("[cancel] cancelling progress_message_id=%s", progress_id)
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
    bot: BotClient,
    enqueue: Callable[[int, int, str, ResumeToken], Awaitable[None]],
    running_task: RunningTask,
    chat_id: int,
    user_msg_id: int,
    text: str,
) -> None:
    resume = await _wait_for_resume(running_task)
    if resume is None:
        await bot.send_message(
            chat_id=chat_id,
            text="resume token not ready yet; try replying to the final message.",
            reply_to_message_id=user_msg_id,
            disable_notification=True,
        )
        return
    await enqueue(chat_id, user_msg_id, text, resume)


async def _run_main_loop(
    cfg: BridgeConfig,
    poller: Callable[[BridgeConfig], AsyncIterator[dict[str, Any]]] = poll_updates,
) -> None:
    running_tasks: dict[int, RunningTask] = {}

    try:
        async with anyio.create_task_group() as tg:
            scheduler_lock = anyio.Lock()

            @dataclass(frozen=True, slots=True)
            class ThreadJob:
                chat_id: int
                user_msg_id: int
                text: str
                resume_token: ResumeToken

            pending_by_thread: dict[str, deque[ThreadJob]] = {}
            active_threads: set[str] = set()
            busy_until: dict[str, anyio.Event] = {}

            def thread_key(token: ResumeToken) -> str:
                return f"{token.engine}:{token.value}"

            async def clear_busy(key: str, done: anyio.Event) -> None:
                await done.wait()
                async with scheduler_lock:
                    if busy_until.get(key) is done:
                        busy_until.pop(key, None)

            async def note_thread_known(token: ResumeToken, done: anyio.Event) -> None:
                key = thread_key(token)
                async with scheduler_lock:
                    current = busy_until.get(key)
                    if current is None or current.is_set():
                        busy_until[key] = done
                tg.start_soon(clear_busy, key, done)

            async def run_job(
                chat_id: int,
                user_msg_id: int,
                text: str,
                resume_token: ResumeToken | None,
                on_thread_known: Callable[[ResumeToken, anyio.Event], Awaitable[None]]
                | None = None,
            ) -> None:
                try:
                    await handle_message(
                        cfg,
                        chat_id=chat_id,
                        user_msg_id=user_msg_id,
                        text=text,
                        resume_token=resume_token,
                        running_tasks=running_tasks,
                        on_thread_known=on_thread_known,
                        progress_edit_every=cfg.progress_edit_every,
                    )
                except Exception:
                    logger.exception("[handle] worker failed")

            async def thread_worker(key: str) -> None:
                try:
                    while True:
                        async with scheduler_lock:
                            done = busy_until.get(key)
                            queue = pending_by_thread.get(key)
                            if not queue:
                                pending_by_thread.pop(key, None)
                                active_threads.discard(key)
                                return
                            job = queue.popleft()

                        if done is not None and not done.is_set():
                            await done.wait()

                        await run_job(
                            job.chat_id,
                            job.user_msg_id,
                            job.text,
                            job.resume_token,
                        )
                finally:
                    async with scheduler_lock:
                        active_threads.discard(key)

            async def enqueue(
                chat_id: int,
                user_msg_id: int,
                text: str,
                resume_token: ResumeToken,
            ) -> None:
                key = thread_key(resume_token)
                async with scheduler_lock:
                    queue = pending_by_thread.get(key)
                    if queue is None:
                        queue = deque()
                        pending_by_thread[key] = queue
                    queue.append(
                        ThreadJob(
                            chat_id=chat_id,
                            user_msg_id=user_msg_id,
                            text=text,
                            resume_token=resume_token,
                        )
                    )
                    if key in active_threads:
                        return
                    active_threads.add(key)
                tg.start_soon(thread_worker, key)

            async for msg in poller(cfg):
                text = msg["text"]
                user_msg_id = msg["message_id"]

                if _is_cancel_command(text):
                    tg.start_soon(_handle_cancel, cfg, msg, running_tasks)
                    continue

                r = msg.get("reply_to_message") or {}
                resume_token = _resolve_resume(cfg.runner, text, r.get("text"))
                reply_id = r.get("message_id")
                if resume_token is None and reply_id is not None:
                    running_task = running_tasks.get(int(reply_id))
                    if running_task is not None:
                        tg.start_soon(
                            _send_with_resume,
                            cfg.bot,
                            enqueue,
                            running_task,
                            msg["chat"]["id"],
                            user_msg_id,
                            text,
                        )
                        continue

                if resume_token is None:
                    tg.start_soon(
                        run_job,
                        msg["chat"]["id"],
                        user_msg_id,
                        text,
                        None,
                        note_thread_known,
                    )
                else:
                    await enqueue(msg["chat"]["id"], user_msg_id, text, resume_token)
    finally:
        await cfg.bot.close()
