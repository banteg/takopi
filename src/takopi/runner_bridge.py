from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import anyio

from .context import RunContext
from .logging import bind_run_context, get_logger
from .model import CompletedEvent, ResumeToken, StartedEvent, TakopiEvent
from .presenter import Presenter
from .markdown import render_event_cli
from .runner import Runner
from .progress import ProgressTracker
from .transport import (
    ChannelId,
    MessageId,
    MessageRef,
    RenderedMessage,
    SendOptions,
    ThreadId,
    Transport,
)

logger = get_logger(__name__)


def _log_runner_event(evt: TakopiEvent) -> None:
    for line in render_event_cli(evt):
        logger.debug(
            "runner.event.cli",
            line=line,
            event_type=getattr(evt, "type", None),
            engine=getattr(evt, "engine", None),
        )


def _strip_resume_lines(text: str, *, is_resume_line: Callable[[str], bool]) -> str:
    prompt = "\n".join(
        line for line in text.splitlines() if not is_resume_line(line)
    ).strip()
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


@dataclass(frozen=True, slots=True)
class IncomingMessage:
    channel_id: ChannelId
    message_id: MessageId
    text: str
    reply_to: MessageRef | None = None
    thread_id: ThreadId | None = None


@dataclass(frozen=True, slots=True)
class ExecBridgeConfig:
    transport: Transport
    presenter: Presenter
    final_notify: bool


@dataclass(slots=True)
class RunningTask:
    resume: ResumeToken | None = None
    resume_ready: anyio.Event = field(default_factory=anyio.Event)
    cancel_requested: anyio.Event = field(default_factory=anyio.Event)
    done: anyio.Event = field(default_factory=anyio.Event)
    context: RunContext | None = None


RunningTasks = dict[MessageRef, RunningTask]


async def _send_or_edit_message(
    transport: Transport,
    *,
    channel_id: ChannelId,
    message: RenderedMessage,
    edit_ref: MessageRef | None = None,
    reply_to: MessageRef | None = None,
    notify: bool = True,
    replace_ref: MessageRef | None = None,
    thread_id: ThreadId | None = None,
) -> tuple[MessageRef | None, bool]:
    msg = message
    followups = message.extra.get("followups")
    if followups:
        extra = dict(message.extra)
        if reply_to is not None:
            extra.setdefault("followup_reply_to_message_id", reply_to.message_id)
        if thread_id is not None:
            extra.setdefault("followup_thread_id", thread_id)
        extra.setdefault("followup_notify", notify)
        msg = RenderedMessage(text=message.text, extra=extra)
    if edit_ref is not None:
        logger.debug(
            "transport.edit_message",
            channel_id=edit_ref.channel_id,
            message_id=edit_ref.message_id,
            rendered=msg.text,
        )
        edited = await transport.edit(ref=edit_ref, message=msg)
        if edited is not None:
            return edited, True

    logger.debug(
        "transport.send_message",
        channel_id=channel_id,
        reply_to_message_id=reply_to.message_id if reply_to else None,
        rendered=msg.text,
    )
    sent = await transport.send(
        channel_id=channel_id,
        message=msg,
        options=SendOptions(
            reply_to=reply_to,
            notify=notify,
            replace=replace_ref,
            thread_id=thread_id,
        ),
    )
    return sent, False


class ProgressEdits:
    def __init__(
        self,
        *,
        transport: Transport,
        presenter: Presenter,
        channel_id: ChannelId,
        progress_ref: MessageRef | None,
        tracker: ProgressTracker,
        started_at: float,
        clock: Callable[[], float],
        last_rendered: RenderedMessage | None,
        resume_formatter: Callable[[ResumeToken], str] | None = None,
        label: str = "working",
        context_line: str | None = None,
    ) -> None:
        self.transport = transport
        self.presenter = presenter
        self.channel_id = channel_id
        self.progress_ref = progress_ref
        self.tracker = tracker
        self.started_at = started_at
        self.clock = clock
        self.last_rendered = last_rendered
        self.resume_formatter = resume_formatter
        self.label = label
        self.context_line = context_line
        self.event_seq = 0
        self.rendered_seq = 0
        self.signal_send, self.signal_recv = anyio.create_memory_object_stream(1)

    async def run(self) -> None:
        if self.progress_ref is None:
            return
        while True:
            while self.rendered_seq == self.event_seq:
                try:
                    await self.signal_recv.receive()
                except anyio.EndOfStream:
                    return

            seq_at_render = self.event_seq
            now = self.clock()
            state = self.tracker.snapshot(
                resume_formatter=self.resume_formatter,
                context_line=self.context_line,
            )
            rendered = self.presenter.render_progress(
                state, elapsed_s=now - self.started_at, label=self.label
            )
            if rendered != self.last_rendered:
                logger.debug(
                    "transport.edit_message",
                    channel_id=self.channel_id,
                    message_id=self.progress_ref.message_id,
                    rendered=rendered.text,
                )
                edited = await self.transport.edit(
                    ref=self.progress_ref,
                    message=rendered,
                    wait=False,
                )
                if edited is not None:
                    self.last_rendered = rendered

            self.rendered_seq = seq_at_render

    async def on_event(self, evt: TakopiEvent) -> None:
        if not self.tracker.note_event(evt):
            return
        if self.progress_ref is None:
            return
        self.event_seq += 1
        try:
            self.signal_send.send_nowait(None)
        except anyio.WouldBlock:
            pass
        except (anyio.BrokenResourceError, anyio.ClosedResourceError):
            pass


@dataclass(frozen=True, slots=True)
class ProgressMessageState:
    ref: MessageRef | None
    last_rendered: RenderedMessage | None


async def send_initial_progress(
    cfg: ExecBridgeConfig,
    *,
    channel_id: ChannelId,
    reply_to: MessageRef,
    label: str,
    tracker: ProgressTracker,
    progress_ref: MessageRef | None = None,
    resume_formatter: Callable[[ResumeToken], str] | None = None,
    context_line: str | None = None,
    thread_id: ThreadId | None = None,
) -> ProgressMessageState:
    last_rendered: RenderedMessage | None = None

    state = tracker.snapshot(
        resume_formatter=resume_formatter,
        context_line=context_line,
    )
    initial_rendered = cfg.presenter.render_progress(
        state,
        elapsed_s=0.0,
        label=label,
    )
    sent_ref, _ = await _send_or_edit_message(
        cfg.transport,
        channel_id=channel_id,
        message=initial_rendered,
        edit_ref=progress_ref,
        reply_to=reply_to,
        notify=False,
        replace_ref=progress_ref,
        thread_id=thread_id,
    )
    if sent_ref is not None:
        last_rendered = initial_rendered
        logger.debug(
            "progress.sent",
            channel_id=sent_ref.channel_id,
            message_id=sent_ref.message_id,
        )

    return ProgressMessageState(
        ref=sent_ref,
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
                        bind_run_context(resume=evt.resume.value)
                        if running_task is not None and running_task.resume is None:
                            running_task.resume = evt.resume
                            try:
                                if on_thread_known is not None:
                                    await on_thread_known(evt.resume, running_task.done)
                            finally:
                                running_task.resume_ready.set()
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
    tracker: ProgressTracker, resume: ResumeToken | None
) -> ResumeToken | None:
    resume = resume or tracker.resume
    tracker.set_resume(resume)
    return resume


async def send_result_message(
    cfg: ExecBridgeConfig,
    *,
    channel_id: ChannelId,
    reply_to: MessageRef,
    progress_ref: MessageRef | None,
    message: RenderedMessage,
    notify: bool,
    edit_ref: MessageRef | None,
    replace_ref: MessageRef | None = None,
    delete_tag: str = "final",
    thread_id: ThreadId | None = None,
) -> None:
    final_msg, edited = await _send_or_edit_message(
        cfg.transport,
        channel_id=channel_id,
        message=message,
        edit_ref=edit_ref,
        reply_to=reply_to,
        notify=notify,
        replace_ref=replace_ref,
        thread_id=thread_id,
    )
    if final_msg is None:
        return
    if (
        progress_ref is not None
        and (edit_ref is None or not edited)
        and replace_ref is None
    ):
        logger.debug(
            "transport.delete_message",
            channel_id=progress_ref.channel_id,
            message_id=progress_ref.message_id,
            tag=delete_tag,
        )
        await cfg.transport.delete(ref=progress_ref)


async def handle_message(
    cfg: ExecBridgeConfig,
    *,
    runner: Runner,
    incoming: IncomingMessage,
    resume_token: ResumeToken | None,
    context: RunContext | None = None,
    context_line: str | None = None,
    strip_resume_line: Callable[[str], bool] | None = None,
    running_tasks: RunningTasks | None = None,
    on_thread_known: Callable[[ResumeToken, anyio.Event], Awaitable[None]]
    | None = None,
    progress_ref: MessageRef | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    logger.info(
        "handle.incoming",
        channel_id=incoming.channel_id,
        user_msg_id=incoming.message_id,
        resume=resume_token.value if resume_token else None,
        text=incoming.text,
    )
    started_at = clock()
    is_resume_line = runner.is_resume_line
    resume_strip = strip_resume_line or is_resume_line
    runner_text = _strip_resume_lines(incoming.text, is_resume_line=resume_strip)

    progress_tracker = ProgressTracker(engine=runner.engine)

    user_ref = MessageRef(
        channel_id=incoming.channel_id,
        message_id=incoming.message_id,
    )
    progress_state = await send_initial_progress(
        cfg,
        channel_id=incoming.channel_id,
        reply_to=user_ref,
        label="starting",
        tracker=progress_tracker,
        progress_ref=progress_ref,
        resume_formatter=runner.format_resume,
        context_line=context_line,
        thread_id=incoming.thread_id,
    )
    progress_ref = progress_state.ref

    edits = ProgressEdits(
        transport=cfg.transport,
        presenter=cfg.presenter,
        channel_id=incoming.channel_id,
        progress_ref=progress_ref,
        tracker=progress_tracker,
        started_at=started_at,
        clock=clock,
        last_rendered=progress_state.last_rendered,
        resume_formatter=runner.format_resume,
        context_line=context_line,
    )

    running_task: RunningTask | None = None
    if running_tasks is not None and progress_ref is not None:
        running_task = RunningTask(context=context)
        running_tasks[progress_ref] = running_task

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
        if progress_ref is not None:
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
        except Exception as exc:
            error = exc
            logger.exception(
                "handle.runner_failed",
                error=str(exc),
                error_type=exc.__class__.__name__,
            )
        finally:
            if running_task is not None and running_tasks is not None:
                running_task.done.set()
                if progress_ref is not None:
                    running_tasks.pop(progress_ref, None)
            if not outcome.cancelled and error is None:
                # Give pending progress edits a chance to flush if they're ready.
                await anyio.sleep(0)
            edits_scope.cancel()

    elapsed = clock() - started_at

    if error is not None:
        sync_resume_token(progress_tracker, outcome.resume)
        err_body = _format_error(error)
        state = progress_tracker.snapshot(
            resume_formatter=runner.format_resume,
            context_line=context_line,
        )
        final_rendered = cfg.presenter.render_final(
            state,
            elapsed_s=elapsed,
            status="error",
            answer=err_body,
        )
        logger.debug(
            "handle.error.rendered",
            error=err_body,
            rendered=final_rendered.text,
        )
        await send_result_message(
            cfg,
            channel_id=incoming.channel_id,
            reply_to=user_ref,
            progress_ref=progress_ref,
            message=final_rendered,
            notify=False,
            edit_ref=progress_ref,
            replace_ref=progress_ref,
            delete_tag="error",
            thread_id=incoming.thread_id,
        )
        return

    if outcome.cancelled:
        resume = sync_resume_token(progress_tracker, outcome.resume)
        logger.info(
            "handle.cancelled",
            resume=resume.value if resume else None,
            elapsed_s=elapsed,
        )
        state = progress_tracker.snapshot(
            resume_formatter=runner.format_resume,
            context_line=context_line,
        )
        final_rendered = cfg.presenter.render_progress(
            state,
            elapsed_s=elapsed,
            label="`cancelled`",
        )
        await send_result_message(
            cfg,
            channel_id=incoming.channel_id,
            reply_to=user_ref,
            progress_ref=progress_ref,
            message=final_rendered,
            notify=False,
            edit_ref=progress_ref,
            replace_ref=progress_ref,
            delete_tag="cancel",
            thread_id=incoming.thread_id,
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
    resume_value = None
    resume_token = completed.resume or outcome.resume
    if resume_token is not None:
        resume_value = resume_token.value
    logger.info(
        "runner.completed",
        ok=run_ok,
        error=run_error,
        answer_len=len(final_answer or ""),
        elapsed_s=round(elapsed, 2),
        action_count=progress_tracker.action_count,
        resume=resume_value,
    )
    sync_resume_token(progress_tracker, completed.resume or outcome.resume)
    state = progress_tracker.snapshot(
        resume_formatter=runner.format_resume,
        context_line=context_line,
    )
    final_rendered = cfg.presenter.render_final(
        state,
        elapsed_s=elapsed,
        status=status,
        answer=final_answer,
    )
    logger.debug(
        "handle.final.rendered",
        rendered=final_rendered.text,
        status=status,
    )

    can_edit_final = progress_ref is not None
    edit_ref = None if cfg.final_notify or not can_edit_final else progress_ref

    await send_result_message(
        cfg,
        channel_id=incoming.channel_id,
        reply_to=user_ref,
        progress_ref=progress_ref,
        message=final_rendered,
        notify=cfg.final_notify,
        edit_ref=edit_ref,
        replace_ref=progress_ref,
        delete_tag="final",
        thread_id=incoming.thread_id,
    )
