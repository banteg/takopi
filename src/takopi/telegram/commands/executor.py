from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from functools import partial
from typing import cast

import anyio

from ...commands import CommandExecutor, RunMode, RunRequest, RunResult
from ...config import ConfigError
from ...context import RunContext
from ...logging import bind_run_context, clear_context, get_logger
from ...model import Action, ActionEvent, EngineId, ResumeToken, TakopiEvent
from ...progress import ProgressTracker
from ...router import RunnerUnavailableError
from ...runner import Runner
from ...runners.run_options import EngineRunOptions, apply_run_options
from ...runner_bridge import (
    ExecBridgeConfig,
    IncomingMessage as RunnerIncomingMessage,
    RunningTasks,
    handle_message,
)
from ...scheduler import ThreadScheduler
from ...transport import MessageRef, RenderedMessage, SendOptions
from ...transport_runtime import TransportRuntime
from ...utils.paths import reset_run_base_dir, set_run_base_dir
from ..bridge import send_plain
from ..engine_overrides import supports_reasoning

logger = get_logger(__name__)


@dataclass(slots=True)
class _ResumeLineProxy:
    runner: Runner

    @property
    def engine(self) -> str:
        return self.runner.engine

    def is_resume_line(self, line: str) -> bool:
        return self.runner.is_resume_line(line)

    def format_resume(self, _: ResumeToken) -> str:
        return ""

    def extract_resume(self, text: str | None) -> ResumeToken | None:
        return self.runner.extract_resume(text)

    def run(
        self, prompt: str, resume: ResumeToken | None
    ) -> AsyncIterator[TakopiEvent]:
        return self.runner.run(prompt, resume)


@dataclass(slots=True)
class _PreludeRunner:
    runner: Runner
    prelude_events: Sequence[TakopiEvent]

    @property
    def engine(self) -> str:
        return self.runner.engine

    def is_resume_line(self, line: str) -> bool:
        return self.runner.is_resume_line(line)

    def format_resume(self, token: ResumeToken) -> str:
        return self.runner.format_resume(token)

    def extract_resume(self, text: str | None) -> ResumeToken | None:
        return self.runner.extract_resume(text)

    async def run(
        self, prompt: str, resume: ResumeToken | None
    ) -> AsyncIterator[TakopiEvent]:
        for event in self.prelude_events:
            yield event
        async for event in self.runner.run(prompt, resume):
            yield event


def _reasoning_warning(
    *, engine: str, run_options: EngineRunOptions | None
) -> ActionEvent | None:
    if run_options is None or not run_options.reasoning:
        return None
    if supports_reasoning(engine):
        return None
    message = f"reasoning override is not supported for `{engine}`; ignoring."
    return ActionEvent(
        engine=engine,
        action=Action(
            id=f"{engine}.override.reasoning",
            kind="note",
            title=message,
            detail={},
        ),
        phase="completed",
        ok=True,
    )


def _should_show_resume_line(
    *,
    show_resume_line: bool,
    stateful_mode: bool,
    context: RunContext | None,
) -> bool:
    if show_resume_line:
        return True
    return not stateful_mode


async def _send_runner_unavailable(
    exec_cfg: ExecBridgeConfig,
    *,
    chat_id: int,
    user_msg_id: int,
    resume_token: ResumeToken | None,
    runner: Runner,
    reason: str,
    thread_id: int | None = None,
) -> None:
    tracker = ProgressTracker(engine=runner.engine)
    tracker.set_resume(resume_token)
    state = tracker.snapshot(resume_formatter=runner.format_resume)
    message = exec_cfg.presenter.render_final(
        state,
        elapsed_s=0.0,
        status="error",
        answer=f"error:\n{reason}",
    )
    reply_to = MessageRef(channel_id=chat_id, message_id=user_msg_id)
    await exec_cfg.transport.send(
        channel_id=chat_id,
        message=message,
        options=SendOptions(reply_to=reply_to, notify=True, thread_id=thread_id),
    )


async def _run_engine(
    *,
    exec_cfg: ExecBridgeConfig,
    runtime: TransportRuntime,
    running_tasks: RunningTasks | None,
    chat_id: int,
    user_msg_id: int,
    text: str,
    resume_token: ResumeToken | None,
    context: RunContext | None,
    reply_ref: MessageRef | None = None,
    on_thread_known: Callable[[ResumeToken, anyio.Event], Awaitable[None]]
    | None = None,
    engine_override: EngineId | None = None,
    thread_id: int | None = None,
    show_resume_line: bool = True,
    progress_ref: MessageRef | None = None,
    run_options: EngineRunOptions | None = None,
) -> None:
    reply = partial(
        send_plain,
        exec_cfg.transport,
        chat_id=chat_id,
        user_msg_id=user_msg_id,
        thread_id=thread_id,
    )
    try:
        try:
            entry = runtime.resolve_runner(
                resume_token=resume_token,
                engine_override=engine_override,
            )
        except RunnerUnavailableError as exc:
            await reply(text=f"error:\n{exc}")
            return
        runner: Runner = entry.runner
        if not show_resume_line:
            runner = cast(Runner, _ResumeLineProxy(runner))
        warning = _reasoning_warning(engine=runner.engine, run_options=run_options)
        if warning is not None:
            runner = cast(Runner, _PreludeRunner(runner, [warning]))
        if not entry.available:
            reason = entry.issue or "engine unavailable"
            await _send_runner_unavailable(
                exec_cfg,
                chat_id=chat_id,
                user_msg_id=user_msg_id,
                resume_token=resume_token,
                runner=runner,
                reason=reason,
                thread_id=thread_id,
            )
            return
        try:
            cwd = runtime.resolve_run_cwd(context)
        except ConfigError as exc:
            await reply(text=f"error:\n{exc}")
            return
        run_base_token = set_run_base_dir(cwd)
        try:
            run_fields = {
                "chat_id": chat_id,
                "user_msg_id": user_msg_id,
                "engine": runner.engine,
                "resume": resume_token.value if resume_token else None,
            }
            if context is not None:
                run_fields["project"] = context.project
                run_fields["branch"] = context.branch
            if cwd is not None:
                run_fields["cwd"] = str(cwd)
            bind_run_context(**run_fields)
            context_line = runtime.format_context_line(context)
            incoming = RunnerIncomingMessage(
                channel_id=chat_id,
                message_id=user_msg_id,
                text=text,
                reply_to=reply_ref,
                thread_id=thread_id,
            )
            with apply_run_options(run_options):
                await handle_message(
                    exec_cfg,
                    runner=runner,
                    incoming=incoming,
                    resume_token=resume_token,
                    context=context,
                    context_line=context_line,
                    strip_resume_line=runtime.is_resume_line,
                    running_tasks=running_tasks,
                    on_thread_known=on_thread_known,
                    progress_ref=progress_ref,
                )
        finally:
            reset_run_base_dir(run_base_token)
    except Exception as exc:
        logger.exception(
            "handle.worker_failed",
            error=str(exc),
            error_type=exc.__class__.__name__,
        )
    finally:
        clear_context()


class _CaptureTransport:
    def __init__(self) -> None:
        self._next_id = 1
        self.last_message: RenderedMessage | None = None

    async def send(
        self,
        *,
        channel_id: int | str,
        message: RenderedMessage,
        options: SendOptions | None = None,
    ) -> MessageRef:
        thread_id = options.thread_id if options is not None else None
        ref = MessageRef(channel_id=channel_id, message_id=self._next_id)
        self._next_id += 1
        self.last_message = message
        return MessageRef(
            channel_id=ref.channel_id,
            message_id=ref.message_id,
            thread_id=thread_id,
        )

    async def edit(
        self, *, ref: MessageRef, message: RenderedMessage, wait: bool = True
    ) -> MessageRef:
        self.last_message = message
        return ref

    async def delete(self, *, ref: MessageRef) -> bool:
        return True

    async def close(self) -> None:
        return None


class _TelegramCommandExecutor(CommandExecutor):
    def __init__(
        self,
        *,
        exec_cfg: ExecBridgeConfig,
        runtime: TransportRuntime,
        running_tasks: RunningTasks,
        scheduler: ThreadScheduler,
        on_thread_known: Callable[[ResumeToken, anyio.Event], Awaitable[None]] | None,
        engine_overrides_resolver: Callable[
            [EngineId], Awaitable[EngineRunOptions | None]
        ]
        | None,
        chat_id: int,
        user_msg_id: int,
        thread_id: int | None,
        show_resume_line: bool,
        stateful_mode: bool,
        default_engine_override: EngineId | None,
    ) -> None:
        self._exec_cfg = exec_cfg
        self._runtime = runtime
        self._running_tasks = running_tasks
        self._scheduler = scheduler
        self._on_thread_known = on_thread_known
        self._engine_overrides_resolver = engine_overrides_resolver
        self._chat_id = chat_id
        self._user_msg_id = user_msg_id
        self._thread_id = thread_id
        self._show_resume_line = show_resume_line
        self._stateful_mode = stateful_mode
        self._default_engine_override = default_engine_override
        self._reply_ref = MessageRef(
            channel_id=chat_id,
            message_id=user_msg_id,
            thread_id=thread_id,
        )

    def _apply_default_context(self, request: RunRequest) -> RunRequest:
        if request.context is not None:
            return request
        context = self._runtime.default_context_for_chat(self._chat_id)
        if context is None:
            return request
        return RunRequest(
            prompt=request.prompt,
            engine=request.engine,
            context=context,
        )

    def _apply_default_engine(self, request: RunRequest) -> RunRequest:
        if request.engine is not None or self._default_engine_override is None:
            return request
        return RunRequest(
            prompt=request.prompt,
            engine=self._default_engine_override,
            context=request.context,
        )

    async def send(
        self,
        message: RenderedMessage | str,
        *,
        reply_to: MessageRef | None = None,
        notify: bool = True,
    ) -> MessageRef | None:
        rendered = (
            message
            if isinstance(message, RenderedMessage)
            else RenderedMessage(text=message)
        )
        reply_ref = self._reply_ref if reply_to is None else reply_to
        return await self._exec_cfg.transport.send(
            channel_id=self._chat_id,
            message=rendered,
            options=SendOptions(
                reply_to=reply_ref,
                notify=notify,
                thread_id=self._thread_id,
            ),
        )

    async def run_one(
        self, request: RunRequest, *, mode: RunMode = "emit"
    ) -> RunResult:
        request = self._apply_default_context(request)
        request = self._apply_default_engine(request)
        effective_show_resume_line = _should_show_resume_line(
            show_resume_line=self._show_resume_line,
            stateful_mode=self._stateful_mode,
            context=request.context,
        )
        engine = self._runtime.resolve_engine(
            engine_override=request.engine,
            context=request.context,
        )
        run_options = None
        if self._engine_overrides_resolver is not None:
            run_options = await self._engine_overrides_resolver(engine)
        on_thread_known = (
            self._scheduler.note_thread_known
            if self._on_thread_known is None
            else self._on_thread_known
        )
        if mode == "capture":
            capture = _CaptureTransport()
            exec_cfg = ExecBridgeConfig(
                transport=capture,
                presenter=self._exec_cfg.presenter,
                final_notify=False,
            )
            await _run_engine(
                exec_cfg=exec_cfg,
                runtime=self._runtime,
                running_tasks={},
                chat_id=self._chat_id,
                user_msg_id=self._user_msg_id,
                text=request.prompt,
                resume_token=None,
                context=request.context,
                reply_ref=self._reply_ref,
                on_thread_known=on_thread_known,
                engine_override=engine,
                thread_id=self._thread_id,
                show_resume_line=effective_show_resume_line,
                run_options=run_options,
            )
            return RunResult(engine=engine, message=capture.last_message)
        await _run_engine(
            exec_cfg=self._exec_cfg,
            runtime=self._runtime,
            running_tasks=self._running_tasks,
            chat_id=self._chat_id,
            user_msg_id=self._user_msg_id,
            text=request.prompt,
            resume_token=None,
            context=request.context,
            reply_ref=self._reply_ref,
            on_thread_known=on_thread_known,
            engine_override=engine,
            thread_id=self._thread_id,
            show_resume_line=effective_show_resume_line,
            run_options=run_options,
        )
        return RunResult(engine=engine, message=None)

    async def run_many(
        self,
        requests: Sequence[RunRequest],
        *,
        mode: RunMode = "emit",
        parallel: bool = False,
    ) -> list[RunResult]:
        if not parallel:
            return [await self.run_one(request, mode=mode) for request in requests]
        results: list[RunResult | None] = [None] * len(requests)

        async with anyio.create_task_group() as tg:

            async def run_idx(idx: int, request: RunRequest) -> None:
                results[idx] = await self.run_one(request, mode=mode)

            for idx, request in enumerate(requests):
                tg.start_soon(run_idx, idx, request)

        return [result for result in results if result is not None]
