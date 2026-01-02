"""Telegram bridge orchestration for running runners and streaming progress."""

from __future__ import annotations

import logging
import time
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import anyio

from .model import CompletedEvent, EngineId, ResumeToken, StartedEvent, TakopiEvent
from .render import (
    ExecProgressRenderer,
    MarkdownParts,
    assemble_markdown_parts,
    prepare_telegram,
    render_event_cli,
)
from .router import AutoRouter, RunnerUnavailableError
from .runner import Runner
from .telegram import BotClient


logger = logging.getLogger(__name__)


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
    commands: list[dict[str, str]] = [
        {"command": "cancel", "description": "cancel run"}
    ]
    seen = {"cancel"}
    for engine in router.engine_ids:
        cmd = engine.lower()
        if cmd in seen:
            continue
        commands.append({"command": cmd, "description": f"start {cmd}"})
        seen.add(cmd)
    return commands


async def _set_command_menu(cfg: BridgeConfig) -> None:
    commands = _build_bot_commands(cfg.router)
    if not commands:
        return
    try:
        ok = await cfg.bot.set_my_commands(commands)
    except Exception as exc:
        logger.info("[startup] command menu update failed: %s", exc)
        return
    if not ok:
        logger.info("[startup] command menu update rejected")
        return
    logger.info(
        "[startup] command menu updated commands=%s",
        ", ".join(cmd["command"] for cmd in commands),
    )


def _strip_resume_lines(text: str, *, is_resume_line: Callable[[str], bool]) -> str:
    stripped_lines: list[str] = []
    for line in text.splitlines():
        if is_resume_line(line):
            continue
        stripped_lines.append(line)
    prompt = "\n".join(stripped_lines).strip()
    return prompt or "continue"


def _flatten_exception_group(error: BaseException) -> list[BaseException]:
    if isinstance(error, BaseExceptionGroup):
        flattened: list[BaseException] = []
        for exc in error.exceptions:
            flattened.extend(_flatten_exception_group(exc))
        return flattened
    return [error]


def _format_error(error: Exception) -> str:
    cancel_exc = anyio.get_cancelled_exc_class()
    flattened = [
        exc
        for exc in _flatten_exception_group(error)
        if not isinstance(exc, cancel_exc)
    ]
    if len(flattened) == 1:
        return str(flattened[0]) or flattened[0].__class__.__name__
    if not flattened:
        return str(error) or error.__class__.__name__
    messages = [str(exc) for exc in flattened if str(exc)]
    if not messages:
        return str(error) or error.__class__.__name__
    if len(messages) == 1:
        return messages[0]
    return "\n".join(messages)


PROGRESS_EDIT_EVERY_S = 2.0


async def _send_or_edit_markdown(
    bot: BotClient,
    *,
    chat_id: int,
    parts: MarkdownParts,
    edit_message_id: int | None = None,
    reply_to_message_id: int | None = None,
    disable_notification: bool = False,
    prepared: tuple[str, list[dict[str, Any]]] | None = None,
) -> tuple[dict[str, Any] | None, bool]:
    if prepared is None:
        rendered, entities = prepare_telegram(parts)
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
        last_edit_at: float,
        last_rendered: str | None,
    ) -> None:
        self.bot = bot
        self.chat_id = chat_id
        self.progress_id = progress_id
        self.renderer = renderer
        self.started_at = started_at
        self.progress_edit_every = progress_edit_every
        self.clock = clock
        self.sleep = sleep
        self.last_edit_at = last_edit_at
        self.last_rendered = last_rendered
        self.event_seq = 0
        self.rendered_seq = 0
        self.signal_send, self.signal_recv = anyio.create_memory_object_stream(1)

    async def run(self) -> None:
        if self.progress_id is None:
            return
        while True:
            while self.rendered_seq == self.event_seq:
                try:
                    await self.signal_recv.receive()
                except anyio.EndOfStream:
                    return

            await self.sleep(
                max(
                    0.0,
                    self.last_edit_at + self.progress_edit_every - self.clock(),
                )
            )

            seq_at_render = self.event_seq
            now = self.clock()
            parts = self.renderer.render_progress_parts(now - self.started_at)
            md = assemble_markdown_parts(parts)
            rendered, entities = prepare_telegram(parts)
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

            self.rendered_seq = seq_at_render

    async def on_event(self, evt: TakopiEvent) -> None:
        if not self.renderer.note_event(evt):
            return
        if self.progress_id is None:
            return
        self.event_seq += 1
        try:
            self.signal_send.send_nowait(None)
        except anyio.WouldBlock:
            pass
        except (anyio.BrokenResourceError, anyio.ClosedResourceError):
            pass


@dataclass(frozen=True)
class BridgeConfig:
    bot: BotClient
    router: AutoRouter
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
        parts=MarkdownParts(header=cfg.startup_msg),
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


@dataclass(frozen=True, slots=True)
class ProgressMessageState:
    message_id: int | None
    last_edit_at: float
    last_rendered: str | None


async def send_initial_progress(
    cfg: BridgeConfig,
    *,
    chat_id: int,
    user_msg_id: int,
    label: str,
    renderer: ExecProgressRenderer,
    clock: Callable[[], float],
) -> ProgressMessageState:
    progress_id: int | None = None
    last_edit_at = 0.0
    last_rendered: str | None = None

    initial_parts = renderer.render_progress_parts(0.0, label=label)
    initial_md = assemble_markdown_parts(initial_parts)
    initial_rendered, initial_entities = prepare_telegram(initial_parts)
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

    return ProgressMessageState(
        message_id=progress_id,
        last_edit_at=last_edit_at,
        last_rendered=last_rendered,
    )


@dataclass(slots=True)
class RunOutcome:
    cancelled: bool = False
    completed: CompletedEvent | None = None
    resume: ResumeToken | None = None


async def run_runner_with_cancel(
    runner: Runner,
    *,
    prompt: str,
    resume_token: ResumeToken | None,
    edits: ProgressEdits,
    running_task: RunningTask | None,
    on_thread_known: Callable[[ResumeToken, anyio.Event], Awaitable[None]] | None,
) -> RunOutcome:
    outcome = RunOutcome()
    async with anyio.create_task_group() as tg:

        async def run_runner() -> None:
            try:
                async for evt in runner.run(prompt, resume_token):
                    _log_runner_event(evt)
                    if isinstance(evt, StartedEvent):
                        outcome.resume = evt.resume
                        if running_task is not None and running_task.resume is None:
                            running_task.resume = evt.resume
                            running_task.resume_ready.set()
                            if on_thread_known is not None:
                                await on_thread_known(evt.resume, running_task.done)
                    elif isinstance(evt, CompletedEvent):
                        outcome.resume = evt.resume or outcome.resume
                        outcome.completed = evt
                    await edits.on_event(evt)
            finally:
                tg.cancel_scope.cancel()

        async def wait_cancel(task: RunningTask) -> None:
            await task.cancel_requested.wait()
            outcome.cancelled = True
            tg.cancel_scope.cancel()

        tg.start_soon(run_runner)
        if running_task is not None:
            tg.start_soon(wait_cancel, running_task)

    return outcome


def sync_resume_token(
    renderer: ExecProgressRenderer, resume: ResumeToken | None
) -> ResumeToken | None:
    resume = resume or renderer.resume_token
    renderer.resume_token = resume
    return resume


async def send_result_message(
    cfg: BridgeConfig,
    *,
    chat_id: int,
    user_msg_id: int,
    progress_id: int | None,
    parts: MarkdownParts,
    disable_notification: bool,
    edit_message_id: int | None,
    prepared: tuple[str, list[dict[str, Any]]] | None = None,
    delete_tag: str = "final",
) -> None:
    final_msg, edited = await _send_or_edit_markdown(
        cfg.bot,
        chat_id=chat_id,
        parts=parts,
        edit_message_id=edit_message_id,
        reply_to_message_id=user_msg_id,
        disable_notification=disable_notification,
        prepared=prepared,
    )
    if final_msg is None:
        return
    if progress_id is not None and (edit_message_id is None or not edited):
        logger.debug("[%s] delete progress message_id=%s", delete_tag, progress_id)
        await cfg.bot.delete_message(chat_id=chat_id, message_id=progress_id)


async def handle_message(
    cfg: BridgeConfig,
    *,
    runner: Runner,
    chat_id: int,
    user_msg_id: int,
    text: str,
    resume_token: ResumeToken | None,
    strip_resume_line: Callable[[str], bool] | None = None,
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
    is_resume_line = runner.is_resume_line
    resume_strip = strip_resume_line or is_resume_line
    runner_text = _strip_resume_lines(text, is_resume_line=resume_strip)

    progress_renderer = ExecProgressRenderer(
        max_actions=5, resume_formatter=runner.format_resume, engine=runner.engine
    )

    progress_state = await send_initial_progress(
        cfg,
        chat_id=chat_id,
        user_msg_id=user_msg_id,
        label="starting",
        renderer=progress_renderer,
        clock=clock,
    )
    progress_id = progress_state.message_id

    edits = ProgressEdits(
        bot=cfg.bot,
        chat_id=chat_id,
        progress_id=progress_id,
        renderer=progress_renderer,
        started_at=started_at,
        progress_edit_every=progress_edit_every,
        clock=clock,
        sleep=sleep,
        last_edit_at=progress_state.last_edit_at,
        last_rendered=progress_state.last_rendered,
    )

    running_task: RunningTask | None = None
    if running_tasks is not None and progress_id is not None:
        running_task = RunningTask()
        running_tasks[progress_id] = running_task

    cancel_exc_type = anyio.get_cancelled_exc_class()
    edits_scope = anyio.CancelScope()

    async def run_edits() -> None:
        try:
            with edits_scope:
                await edits.run()
        except cancel_exc_type:
            # Edits are best-effort; cancellation should not bubble into the task group.
            return

    outcome = RunOutcome()
    error: Exception | None = None

    async with anyio.create_task_group() as tg:
        if progress_id is not None:
            tg.start_soon(run_edits)

        try:
            outcome = await run_runner_with_cancel(
                runner,
                prompt=runner_text,
                resume_token=resume_token,
                edits=edits,
                running_task=running_task,
                on_thread_known=on_thread_known,
            )
        except Exception as e:
            error = e
            logger.exception("[handle] runner failed")
        finally:
            if (
                running_task is not None
                and running_tasks is not None
                and progress_id is not None
            ):
                running_task.done.set()
                running_tasks.pop(progress_id, None)
            if not outcome.cancelled and error is None:
                # Give pending progress edits a chance to flush if they're ready.
                await anyio.sleep(0)
            edits_scope.cancel()

    elapsed = clock() - started_at

    if error is not None:
        sync_resume_token(progress_renderer, outcome.resume)
        err_body = _format_error(error)
        final_parts = progress_renderer.render_final_parts(
            elapsed, err_body, status="error"
        )
        logger.debug(
            "[error] markdown: %s",
            assemble_markdown_parts(final_parts),
        )
        await send_result_message(
            cfg,
            chat_id=chat_id,
            user_msg_id=user_msg_id,
            progress_id=progress_id,
            parts=final_parts,
            disable_notification=True,
            edit_message_id=progress_id,
            delete_tag="error",
        )
        return

    if outcome.cancelled:
        resume = sync_resume_token(progress_renderer, outcome.resume)
        logger.info(
            "[handle] cancelled resume=%s elapsed=%.1fs",
            resume.value if resume else None,
            elapsed,
        )
        final_parts = progress_renderer.render_progress_parts(
            elapsed, label="`cancelled`"
        )
        await send_result_message(
            cfg,
            chat_id=chat_id,
            user_msg_id=user_msg_id,
            progress_id=progress_id,
            parts=final_parts,
            disable_notification=True,
            edit_message_id=progress_id,
            delete_tag="cancel",
        )
        return

    if outcome.completed is None:
        raise RuntimeError("runner finished without a completed event")

    completed = outcome.completed
    run_ok = completed.ok
    run_error = completed.error

    final_answer = completed.answer
    if run_ok is False and run_error:
        if final_answer.strip():
            final_answer = f"{final_answer}\n\n{run_error}"
        else:
            final_answer = str(run_error)

    status = (
        "error" if run_ok is False else ("done" if final_answer.strip() else "error")
    )
    sync_resume_token(progress_renderer, completed.resume or outcome.resume)
    final_parts = progress_renderer.render_final_parts(
        elapsed, final_answer, status=status
    )
    logger.debug(
        "[final] markdown: %s",
        assemble_markdown_parts(final_parts),
    )

    final_rendered, final_entities = prepare_telegram(final_parts)
    can_edit_final = progress_id is not None
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

    await send_result_message(
        cfg,
        chat_id=chat_id,
        user_msg_id=user_msg_id,
        progress_id=progress_id,
        parts=final_parts,
        disable_notification=False,
        edit_message_id=edit_message_id,
        prepared=(final_rendered, final_entities),
        delete_tag="final",
    )


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


async def _send_runner_unavailable(
    cfg: BridgeConfig,
    *,
    chat_id: int,
    user_msg_id: int,
    resume_token: ResumeToken | None,
    runner: Runner,
    reason: str,
) -> None:
    progress_renderer = ExecProgressRenderer(
        max_actions=0, resume_formatter=runner.format_resume, engine=runner.engine
    )
    if resume_token is not None:
        progress_renderer.resume_token = resume_token
    final_parts = progress_renderer.render_final_parts(
        0.0, f"error:\n{reason}", status="error"
    )
    await _send_or_edit_markdown(
        cfg.bot,
        chat_id=chat_id,
        parts=final_parts,
        reply_to_message_id=user_msg_id,
        disable_notification=False,
    )


async def run_main_loop(
    cfg: BridgeConfig,
    poller: Callable[[BridgeConfig], AsyncIterator[dict[str, Any]]] = poll_updates,
) -> None:
    running_tasks: dict[int, RunningTask] = {}

    try:
        await _set_command_menu(cfg)
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
                        await _send_or_edit_markdown(
                            cfg.bot,
                            chat_id=chat_id,
                            parts=MarkdownParts(header=f"error:\n{exc}"),
                            reply_to_message_id=user_msg_id,
                            disable_notification=False,
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
                    await handle_message(
                        cfg,
                        runner=entry.runner,
                        chat_id=chat_id,
                        user_msg_id=user_msg_id,
                        text=text,
                        resume_token=resume_token,
                        strip_resume_line=cfg.router.is_resume_line,
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

                text, engine_override = _strip_engine_command(
                    text, engine_ids=cfg.router.engine_ids
                )

                r = msg.get("reply_to_message") or {}
                resume_token = cfg.router.resolve_resume(text, r.get("text"))
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
                        engine_override,
                    )
                else:
                    await enqueue(msg["chat"]["id"], user_msg_id, text, resume_token)
    finally:
        await cfg.bot.close()
