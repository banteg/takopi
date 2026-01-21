from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, cast

import anyio
from anyio.abc import TaskGroup

from ..config import ConfigError
from ..config_watch import ConfigReload, watch_config as watch_config_changes
from ..commands import list_command_ids
from ..directives import DirectiveError
from ..logging import get_logger
from ..model import EngineId, ResumeToken
from ..runners.run_options import EngineRunOptions
from ..scheduler import ThreadJob, ThreadScheduler
from ..progress import ProgressTracker
from ..settings import TelegramTransportSettings
from ..transport import MessageRef, SendOptions
from ..transport_runtime import ResolvedMessage
from ..context import RunContext
from ..ids import RESERVED_CHAT_COMMANDS
from .bridge import CANCEL_CALLBACK_DATA, TelegramBridgeConfig, send_plain
from .commands.cancel import handle_callback_cancel, handle_cancel
from .commands.file_transfer import FILE_PUT_USAGE
from .commands.handlers import (
    dispatch_command,
    handle_agent_command,
    handle_chat_ctx_command,
    handle_chat_new_command,
    handle_ctx_command,
    handle_file_command,
    handle_file_put_default,
    handle_media_group,
    handle_model_command,
    handle_new_command,
    handle_reasoning_command,
    handle_topic_command,
    handle_trigger_command,
    parse_slash_command,
    get_reserved_commands,
    run_engine,
    save_file_put,
    set_command_menu,
    should_show_resume_line,
)
from .commands.parse import is_cancel_command
from .commands.reply import make_reply
from .context import _merge_topic_context, _usage_ctx_set, _usage_topic
from .topics import (
    _maybe_rename_topic,
    _resolve_topics_scope,
    _topic_key,
    _topics_chat_allowed,
    _topics_chat_project,
    _validate_topics_setup,
)
from .client import poll_incoming
from .chat_prefs import ChatPrefsStore, resolve_prefs_path
from .chat_sessions import ChatSessionStore, resolve_sessions_path
from .engine_overrides import merge_overrides
from .engine_defaults import resolve_engine_for_message
from .topic_state import TopicStateStore, resolve_state_path
from .trigger_mode import resolve_trigger_mode, should_trigger_run
from .types import (
    TelegramCallbackQuery,
    TelegramIncomingMessage,
    TelegramIncomingUpdate,
)
from .voice import transcribe_voice

logger = get_logger(__name__)

__all__ = ["poll_updates", "run_main_loop", "send_with_resume"]

ForwardKey = tuple[int, int, int]

_handle_file_put_default = handle_file_put_default


def _chat_session_key(
    msg: TelegramIncomingMessage, *, store: ChatSessionStore | None
) -> tuple[int, int | None] | None:
    if store is None or msg.thread_id is not None:
        return None
    if msg.chat_type == "private":
        return (msg.chat_id, None)
    if msg.sender_id is None:
        return None
    return (msg.chat_id, msg.sender_id)


async def _resolve_engine_run_options(
    chat_id: int,
    thread_id: int | None,
    engine: EngineId,
    chat_prefs: ChatPrefsStore | None,
    topic_store: TopicStateStore | None,
) -> EngineRunOptions | None:
    topic_override = None
    if topic_store is not None and thread_id is not None:
        topic_override = await topic_store.get_engine_override(
            chat_id, thread_id, engine
        )
    chat_override = None
    if chat_prefs is not None:
        chat_override = await chat_prefs.get_engine_override(chat_id, engine)
    merged = merge_overrides(topic_override, chat_override)
    if merged is None:
        return None
    return EngineRunOptions(model=merged.model, reasoning=merged.reasoning)


def _allowed_chat_ids(cfg: TelegramBridgeConfig) -> set[int]:
    allowed = set(cfg.chat_ids or ())
    allowed.add(cfg.chat_id)
    allowed.update(cfg.runtime.project_chat_ids())
    allowed.update(cfg.allowed_user_ids)
    return allowed


async def _send_startup(cfg: TelegramBridgeConfig) -> None:
    from ..markdown import MarkdownParts
    from ..transport import RenderedMessage
    from .render import prepare_telegram

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


def _dispatch_builtin_command(
    *,
    ctx: TelegramCommandContext,
    command_id: str,
) -> bool:
    cfg = ctx.cfg
    msg = ctx.msg
    args_text = ctx.args_text
    ambient_context = ctx.ambient_context
    topic_store = ctx.topic_store
    chat_prefs = ctx.chat_prefs
    resolved_scope = ctx.resolved_scope
    scope_chat_ids = ctx.scope_chat_ids
    reply = ctx.reply
    task_group = ctx.task_group
    if command_id == "file":
        if not cfg.files.enabled:
            handler = partial(
                reply,
                text="file transfer disabled; enable `[transports.telegram.files]`.",
            )
        else:
            handler = partial(
                handle_file_command,
                cfg,
                msg,
                args_text,
                ambient_context,
                topic_store,
            )
        task_group.start_soon(handler)
        return True

    if command_id == "ctx":
        topic_key = (
            _topic_key(msg, cfg, scope_chat_ids=scope_chat_ids)
            if cfg.topics.enabled and topic_store is not None
            else None
        )
        if topic_key is not None:
            handler = partial(
                handle_ctx_command,
                cfg,
                msg,
                args_text,
                topic_store,
                resolved_scope=resolved_scope,
                scope_chat_ids=scope_chat_ids,
            )
        else:
            handler = partial(
                handle_chat_ctx_command,
                cfg,
                msg,
                args_text,
                chat_prefs,
            )
        task_group.start_soon(handler)
        return True

    if cfg.topics.enabled and topic_store is not None:
        if command_id == "new":
            handler = partial(
                handle_new_command,
                cfg,
                msg,
                topic_store,
                resolved_scope=resolved_scope,
                scope_chat_ids=scope_chat_ids,
            )
        elif command_id == "topic":
            handler = partial(
                handle_topic_command,
                cfg,
                msg,
                args_text,
                topic_store,
                resolved_scope=resolved_scope,
                scope_chat_ids=scope_chat_ids,
            )
        else:
            handler = None
        if handler is not None:
            task_group.start_soon(handler)
            return True

    if command_id == "model":
        handler = partial(
            handle_model_command,
            cfg,
            msg,
            args_text,
            ambient_context,
            topic_store,
            chat_prefs,
            resolved_scope=resolved_scope,
            scope_chat_ids=scope_chat_ids,
        )
        task_group.start_soon(handler)
        return True

    if command_id == "agent":
        handler = partial(
            handle_agent_command,
            cfg,
            msg,
            args_text,
            ambient_context,
            topic_store,
            chat_prefs,
            resolved_scope=resolved_scope,
            scope_chat_ids=scope_chat_ids,
        )
        task_group.start_soon(handler)
        return True

    if command_id == "reasoning":
        handler = partial(
            handle_reasoning_command,
            cfg,
            msg,
            args_text,
            ambient_context,
            topic_store,
            chat_prefs,
            resolved_scope=resolved_scope,
            scope_chat_ids=scope_chat_ids,
        )
        task_group.start_soon(handler)
        return True

    if command_id == "trigger":
        handler = partial(
            handle_trigger_command,
            cfg,
            msg,
            args_text,
            ambient_context,
            topic_store,
            chat_prefs,
            resolved_scope=resolved_scope,
            scope_chat_ids=scope_chat_ids,
        )
        task_group.start_soon(handler)
        return True

    return False


async def _drain_backlog(cfg: TelegramBridgeConfig, offset: int | None) -> int | None:
    drained = 0
    while True:
        updates = await cfg.bot.get_updates(
            offset=offset,
            timeout_s=0,
            allowed_updates=["message", "callback_query"],
        )
        if updates is None:
            logger.info("startup.backlog.failed")
            return offset
        logger.debug("startup.backlog.updates", updates=updates)
        if not updates:
            if drained:
                logger.info("startup.backlog.drained", count=drained)
            return offset
        offset = updates[-1].update_id + 1
        drained += len(updates)


async def poll_updates(
    cfg: TelegramBridgeConfig,
    *,
    sleep: Callable[[float], Awaitable[None]] = anyio.sleep,
) -> AsyncIterator[TelegramIncomingUpdate]:
    offset: int | None = None
    offset = await _drain_backlog(cfg, offset)
    await _send_startup(cfg)

    async for msg in poll_incoming(
        cfg.bot,
        chat_ids=lambda: _allowed_chat_ids(cfg),
        offset=offset,
        sleep=sleep,
    ):
        yield msg


@dataclass(slots=True)
class _MediaGroupState:
    messages: list[TelegramIncomingMessage]
    token: int = 0


@dataclass(slots=True)
class _PendingPrompt:
    msg: TelegramIncomingMessage
    text: str
    ambient_context: RunContext | None
    chat_project: str | None
    topic_key: tuple[int, int] | None
    chat_session_key: tuple[int, int | None] | None
    reply_ref: MessageRef | None
    reply_id: int | None
    is_voice_transcribed: bool
    forwards: list[tuple[int, str]]
    cancel_scope: anyio.CancelScope | None = None


@dataclass(frozen=True, slots=True)
class TelegramMsgContext:
    chat_id: int
    thread_id: int | None
    reply_id: int | None
    reply_ref: MessageRef | None
    topic_key: tuple[int, int] | None
    chat_session_key: tuple[int, int | None] | None
    stateful_mode: bool
    chat_project: str | None
    ambient_context: RunContext | None


@dataclass(frozen=True, slots=True)
class TelegramCommandContext:
    cfg: TelegramBridgeConfig
    msg: TelegramIncomingMessage
    args_text: str
    ambient_context: RunContext | None
    topic_store: TopicStateStore | None
    chat_prefs: ChatPrefsStore | None
    resolved_scope: str | None
    scope_chat_ids: frozenset[int]
    reply: Callable[..., Awaitable[None]]
    task_group: TaskGroup


@dataclass(slots=True)
class TelegramLoopState:
    running_tasks: RunningTasks
    pending_prompts: dict[ForwardKey, _PendingPrompt]
    media_groups: dict[tuple[int, str], _MediaGroupState]
    command_ids: set[str]
    reserved_commands: set[str]
    reserved_chat_commands: set[str]
    transport_snapshot: dict[str, object] | None
    topic_store: TopicStateStore | None
    chat_session_store: ChatSessionStore | None
    chat_prefs: ChatPrefsStore | None
    resolved_topics_scope: str | None
    topics_chat_ids: frozenset[int]
    bot_username: str | None
    forward_coalesce_s: float
    media_group_debounce_s: float
    transport_id: str | None


if TYPE_CHECKING:
    from ..runner_bridge import RunningTasks


_FORWARD_FIELDS = (
    "forward_origin",
    "forward_from",
    "forward_from_chat",
    "forward_from_message_id",
    "forward_sender_name",
    "forward_signature",
    "forward_date",
    "is_automatic_forward",
)


def _forward_key(msg: TelegramIncomingMessage) -> ForwardKey:
    return (msg.chat_id, msg.thread_id or 0, msg.sender_id or 0)


def _is_forwarded(raw: dict[str, object] | None) -> bool:
    if not isinstance(raw, dict):
        return False
    return any(raw.get(field) is not None for field in _FORWARD_FIELDS)


def _forward_fields_present(raw: dict[str, object] | None) -> list[str]:
    if not isinstance(raw, dict):
        return []
    return [field for field in _FORWARD_FIELDS if raw.get(field) is not None]


def _format_forwarded_prompt(forwarded: list[str], prompt: str) -> str:
    if not forwarded:
        return prompt
    separator = "\n\n"
    forward_block = separator.join(forwarded)
    if prompt.strip():
        return f"{prompt}{separator}{forward_block}"
    return forward_block


class ForwardCoalescer:
    def __init__(
        self,
        *,
        task_group: TaskGroup,
        debounce_s: float,
        sleep: Callable[[float], Awaitable[None]] = anyio.sleep,
        dispatch: Callable[[_PendingPrompt], Awaitable[None]],
        pending: dict[ForwardKey, _PendingPrompt],
    ) -> None:
        self._task_group = task_group
        self._debounce_s = debounce_s
        self._sleep = sleep
        self._dispatch = dispatch
        self._pending = pending

    def cancel(self, key: ForwardKey) -> None:
        pending = self._pending.pop(key, None)
        if pending is None:
            return
        if pending.cancel_scope is not None:
            pending.cancel_scope.cancel()
        logger.debug(
            "forward.prompt.cancelled",
            chat_id=pending.msg.chat_id,
            thread_id=pending.msg.thread_id,
            sender_id=pending.msg.sender_id,
            message_id=pending.msg.message_id,
            forward_count=len(pending.forwards),
        )

    def schedule(self, pending: _PendingPrompt) -> None:
        if pending.msg.sender_id is None:
            logger.debug(
                "forward.prompt.bypass",
                chat_id=pending.msg.chat_id,
                thread_id=pending.msg.thread_id,
                sender_id=pending.msg.sender_id,
                message_id=pending.msg.message_id,
                reason="missing_sender",
            )
            self._task_group.start_soon(self._dispatch, pending)
            return
        if self._debounce_s <= 0:
            logger.debug(
                "forward.prompt.bypass",
                chat_id=pending.msg.chat_id,
                thread_id=pending.msg.thread_id,
                sender_id=pending.msg.sender_id,
                message_id=pending.msg.message_id,
                reason="disabled",
            )
            self._task_group.start_soon(self._dispatch, pending)
            return
        key = _forward_key(pending.msg)
        existing = self._pending.get(key)
        if existing is not None:
            if existing.cancel_scope is not None:
                existing.cancel_scope.cancel()
            if existing.forwards:
                pending.forwards = list(existing.forwards)
            logger.debug(
                "forward.prompt.replace",
                chat_id=pending.msg.chat_id,
                thread_id=pending.msg.thread_id,
                sender_id=pending.msg.sender_id,
                old_message_id=existing.msg.message_id,
                new_message_id=pending.msg.message_id,
                forward_count=len(pending.forwards),
            )
        self._pending[key] = pending
        logger.debug(
            "forward.prompt.schedule",
            chat_id=pending.msg.chat_id,
            thread_id=pending.msg.thread_id,
            sender_id=pending.msg.sender_id,
            message_id=pending.msg.message_id,
            debounce_s=self._debounce_s,
        )
        self._reschedule(key, pending)

    def attach_forward(self, msg: TelegramIncomingMessage) -> None:
        if msg.sender_id is None:
            logger.debug(
                "forward.message.ignored",
                chat_id=msg.chat_id,
                thread_id=msg.thread_id,
                sender_id=msg.sender_id,
                message_id=msg.message_id,
                reason="missing_sender",
            )
            return
        key = _forward_key(msg)
        pending = self._pending.get(key)
        if pending is None:
            logger.debug(
                "forward.message.ignored",
                chat_id=msg.chat_id,
                thread_id=msg.thread_id,
                sender_id=msg.sender_id,
                message_id=msg.message_id,
                reason="no_pending_prompt",
            )
            return
        text = msg.text
        if not text.strip():
            logger.debug(
                "forward.message.ignored",
                chat_id=msg.chat_id,
                thread_id=msg.thread_id,
                sender_id=msg.sender_id,
                message_id=msg.message_id,
                reason="empty_text",
            )
            return
        pending.forwards.append((msg.message_id, text))
        logger.debug(
            "forward.message.attached",
            chat_id=msg.chat_id,
            thread_id=msg.thread_id,
            sender_id=msg.sender_id,
            message_id=msg.message_id,
            prompt_message_id=pending.msg.message_id,
            forward_count=len(pending.forwards),
            forward_fields=_forward_fields_present(msg.raw),
            forward_date=msg.raw.get("forward_date") if msg.raw else None,
            message_date=msg.raw.get("date") if msg.raw else None,
            text_len=len(text),
        )
        self._reschedule(key, pending)

    def _reschedule(self, key: ForwardKey, pending: _PendingPrompt) -> None:
        if pending.cancel_scope is not None:
            pending.cancel_scope.cancel()
        pending.cancel_scope = None
        self._task_group.start_soon(self._debounce_prompt_run, key, pending)

    async def _debounce_prompt_run(
        self,
        key: ForwardKey,
        pending: _PendingPrompt,
    ) -> None:
        try:
            with anyio.CancelScope() as scope:
                pending.cancel_scope = scope
                await self._sleep(self._debounce_s)
        except anyio.get_cancelled_exc_class():
            return
        if self._pending.get(key) is not pending:
            return
        self._pending.pop(key, None)
        logger.debug(
            "forward.prompt.run",
            chat_id=pending.msg.chat_id,
            thread_id=pending.msg.thread_id,
            sender_id=pending.msg.sender_id,
            message_id=pending.msg.message_id,
            forward_count=len(pending.forwards),
            debounce_s=self._debounce_s,
        )
        await self._dispatch(pending)


@dataclass(frozen=True, slots=True)
class ResumeDecision:
    resume_token: ResumeToken | None
    handled_by_running_task: bool


class ResumeResolver:
    def __init__(
        self,
        *,
        cfg: TelegramBridgeConfig,
        task_group: TaskGroup,
        running_tasks: Mapping[MessageRef, object],
        enqueue_resume: Callable[
            [
                int,
                int,
                str,
                ResumeToken,
                RunContext | None,
                int | None,
                tuple[int, int | None] | None,
                MessageRef | None,
            ],
            Awaitable[None],
        ],
        topic_store: TopicStateStore | None,
        chat_session_store: ChatSessionStore | None,
    ) -> None:
        self._cfg = cfg
        self._task_group = task_group
        self._running_tasks = running_tasks
        self._enqueue_resume = enqueue_resume
        self._topic_store = topic_store
        self._chat_session_store = chat_session_store

    async def resolve(
        self,
        *,
        resume_token: ResumeToken | None,
        reply_id: int | None,
        chat_id: int,
        user_msg_id: int,
        thread_id: int | None,
        chat_session_key: tuple[int, int | None] | None,
        topic_key: tuple[int, int] | None,
        engine_for_session: EngineId,
        prompt_text: str,
    ) -> ResumeDecision:
        if resume_token is not None:
            return ResumeDecision(
                resume_token=resume_token, handled_by_running_task=False
            )
        if reply_id is not None:
            running_task = self._running_tasks.get(
                MessageRef(channel_id=chat_id, message_id=reply_id)
            )
            if running_task is not None:
                self._task_group.start_soon(
                    send_with_resume,
                    self._cfg,
                    self._enqueue_resume,
                    running_task,
                    chat_id,
                    user_msg_id,
                    thread_id,
                    chat_session_key,
                    prompt_text,
                )
                return ResumeDecision(resume_token=None, handled_by_running_task=True)
        if self._topic_store is not None and topic_key is not None:
            stored = await self._topic_store.get_session_resume(
                topic_key[0],
                topic_key[1],
                engine_for_session,
            )
            if stored is not None:
                resume_token = stored
        if (
            resume_token is None
            and self._chat_session_store is not None
            and chat_session_key is not None
        ):
            stored = await self._chat_session_store.get_session_resume(
                chat_session_key[0],
                chat_session_key[1],
                engine_for_session,
            )
            if stored is not None:
                resume_token = stored
        return ResumeDecision(resume_token=resume_token, handled_by_running_task=False)


class MediaGroupBuffer:
    def __init__(
        self,
        *,
        task_group: TaskGroup,
        debounce_s: float,
        sleep: Callable[[float], Awaitable[None]] = anyio.sleep,
        cfg: TelegramBridgeConfig,
        chat_prefs: ChatPrefsStore | None,
        topic_store: TopicStateStore | None,
        bot_username: str | None,
        command_ids: Callable[[], set[str]],
        reserved_chat_commands: set[str],
        groups: dict[tuple[int, str], _MediaGroupState],
        run_prompt_from_upload: Callable[
            [TelegramIncomingMessage, str, ResolvedMessage], Awaitable[None]
        ],
        resolve_prompt_message: Callable[
            [TelegramIncomingMessage, str, RunContext | None],
            Awaitable[ResolvedMessage | None],
        ],
    ) -> None:
        self._task_group = task_group
        self._debounce_s = debounce_s
        self._sleep = sleep
        self._cfg = cfg
        self._chat_prefs = chat_prefs
        self._topic_store = topic_store
        self._bot_username = bot_username
        self._command_ids = command_ids
        self._reserved_chat_commands = reserved_chat_commands
        self._groups = groups
        self._run_prompt_from_upload = run_prompt_from_upload
        self._resolve_prompt_message = resolve_prompt_message

    def add(self, msg: TelegramIncomingMessage) -> None:
        if msg.media_group_id is None:
            return
        key = (msg.chat_id, msg.media_group_id)
        state = self._groups.get(key)
        if state is None:
            state = _MediaGroupState(messages=[])
            self._groups[key] = state
            self._task_group.start_soon(self._flush_media_group, key)
        state.messages.append(msg)
        state.token += 1

    async def _flush_media_group(self, key: tuple[int, str]) -> None:
        while True:
            state = self._groups.get(key)
            if state is None:
                return
            token = state.token
            await self._sleep(self._debounce_s)
            state = self._groups.get(key)
            if state is None:
                return
            if state.token != token:
                continue
            messages = list(state.messages)
            del self._groups[key]
            if not messages:
                return
            trigger_mode = await resolve_trigger_mode(
                chat_id=messages[0].chat_id,
                thread_id=messages[0].thread_id,
                chat_prefs=self._chat_prefs,
                topic_store=self._topic_store,
            )
            command_ids = self._command_ids()
            if trigger_mode == "mentions" and not any(
                should_trigger_run(
                    msg,
                    bot_username=self._bot_username,
                    runtime=self._cfg.runtime,
                    command_ids=command_ids,
                    reserved_chat_commands=self._reserved_chat_commands,
                )
                for msg in messages
            ):
                return
            await handle_media_group(
                self._cfg,
                messages,
                self._topic_store,
                self._run_prompt_from_upload,
                self._resolve_prompt_message,
            )
            return


def _diff_keys(old: dict[str, object], new: dict[str, object]) -> list[str]:
    keys = set(old) | set(new)
    return sorted(key for key in keys if old.get(key) != new.get(key))


async def _wait_for_resume(running_task) -> ResumeToken | None:
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


async def _send_queued_progress(
    cfg: TelegramBridgeConfig,
    *,
    chat_id: int,
    user_msg_id: int,
    thread_id: int | None,
    resume_token: ResumeToken,
    context: RunContext | None,
) -> MessageRef | None:
    tracker = ProgressTracker(engine=resume_token.engine)
    tracker.set_resume(resume_token)
    context_line = cfg.runtime.format_context_line(context)
    state = tracker.snapshot(context_line=context_line)
    message = cfg.exec_cfg.presenter.render_progress(
        state,
        elapsed_s=0.0,
        label="queued",
    )
    reply_ref = MessageRef(
        channel_id=chat_id,
        message_id=user_msg_id,
        thread_id=thread_id,
    )
    return await cfg.exec_cfg.transport.send(
        channel_id=chat_id,
        message=message,
        options=SendOptions(reply_to=reply_ref, notify=False, thread_id=thread_id),
    )


async def send_with_resume(
    cfg: TelegramBridgeConfig,
    enqueue: Callable[
        [
            int,
            int,
            str,
            ResumeToken,
            RunContext | None,
            int | None,
            tuple[int, int | None] | None,
            MessageRef | None,
        ],
        Awaitable[None],
    ],
    running_task,
    chat_id: int,
    user_msg_id: int,
    thread_id: int | None,
    session_key: tuple[int, int | None] | None,
    text: str,
) -> None:
    reply = partial(
        send_plain,
        cfg.exec_cfg.transport,
        chat_id=chat_id,
        user_msg_id=user_msg_id,
        thread_id=thread_id,
    )
    resume = await _wait_for_resume(running_task)
    if resume is None:
        await reply(
            text="resume token not ready yet; try replying to the final message.",
            notify=False,
        )
        return
    progress_ref = await _send_queued_progress(
        cfg,
        chat_id=chat_id,
        user_msg_id=user_msg_id,
        thread_id=thread_id,
        resume_token=resume,
        context=running_task.context,
    )
    await enqueue(
        chat_id,
        user_msg_id,
        text,
        resume,
        running_task.context,
        thread_id,
        session_key,
        progress_ref,
    )


async def run_main_loop(
    cfg: TelegramBridgeConfig,
    poller: Callable[
        [TelegramBridgeConfig], AsyncIterator[TelegramIncomingUpdate]
    ] = poll_updates,
    *,
    watch_config: bool | None = None,
    default_engine_override: str | None = None,
    transport_id: str | None = None,
    transport_config: TelegramTransportSettings | None = None,
    sleep: Callable[[float], Awaitable[None]] = anyio.sleep,
) -> None:
    state = TelegramLoopState(
        running_tasks={},
        pending_prompts={},
        media_groups={},
        command_ids={
            command_id.lower()
            for command_id in list_command_ids(allowlist=cfg.runtime.allowlist)
        },
        reserved_commands=get_reserved_commands(cfg.runtime),
        reserved_chat_commands=set(RESERVED_CHAT_COMMANDS),
        transport_snapshot=(
            transport_config.model_dump() if transport_config is not None else None
        ),
        topic_store=None,
        chat_session_store=None,
        chat_prefs=None,
        resolved_topics_scope=None,
        topics_chat_ids=frozenset(),
        bot_username=None,
        forward_coalesce_s=max(0.0, float(cfg.forward_coalesce_s)),
        media_group_debounce_s=max(0.0, float(cfg.media_group_debounce_s)),
        transport_id=transport_id,
    )

    def refresh_topics_scope() -> None:
        if cfg.topics.enabled:
            (
                state.resolved_topics_scope,
                state.topics_chat_ids,
            ) = _resolve_topics_scope(cfg)
        else:
            state.resolved_topics_scope = None
            state.topics_chat_ids = frozenset()

    def refresh_commands() -> None:
        allowlist = cfg.runtime.allowlist
        state.command_ids = {
            command_id.lower() for command_id in list_command_ids(allowlist=allowlist)
        }
        state.reserved_commands = get_reserved_commands(cfg.runtime)

    try:
        config_path = cfg.runtime.config_path
        if config_path is not None:
            state.chat_prefs = ChatPrefsStore(resolve_prefs_path(config_path))
            logger.info(
                "chat_prefs.enabled",
                state_path=str(resolve_prefs_path(config_path)),
            )
        if cfg.session_mode == "chat":
            if config_path is None:
                raise ConfigError(
                    "session_mode=chat but config path is not set; cannot locate state file."
                )
            state.chat_session_store = ChatSessionStore(
                resolve_sessions_path(config_path)
            )
            cleared = await state.chat_session_store.sync_startup_cwd(Path.cwd())
            if cleared:
                logger.info(
                    "chat_sessions.cleared",
                    reason="startup_cwd_changed",
                    cwd=str(Path.cwd()),
                    state_path=str(resolve_sessions_path(config_path)),
                )
            logger.info(
                "chat_sessions.enabled",
                state_path=str(resolve_sessions_path(config_path)),
            )
        if cfg.topics.enabled:
            if config_path is None:
                raise ConfigError(
                    "topics enabled but config path is not set; cannot locate state file."
                )
            state.topic_store = TopicStateStore(resolve_state_path(config_path))
            await _validate_topics_setup(cfg)
            refresh_topics_scope()
            logger.info(
                "topics.enabled",
                scope=cfg.topics.scope,
                resolved_scope=state.resolved_topics_scope,
                state_path=str(resolve_state_path(config_path)),
            )
        await set_command_menu(cfg)
        try:
            me = await cfg.bot.get_me()
        except Exception as exc:  # noqa: BLE001
            logger.info(
                "trigger_mode.bot_username.failed",
                error=str(exc),
                error_type=exc.__class__.__name__,
            )
            me = None
        if me is not None and me.username:
            state.bot_username = me.username.lower()
        else:
            logger.info("trigger_mode.bot_username.unavailable")
        async with anyio.create_task_group() as tg:
            poller_fn: Callable[
                [TelegramBridgeConfig], AsyncIterator[TelegramIncomingUpdate]
            ]
            if poller is poll_updates:
                poller_fn = cast(
                    Callable[
                        [TelegramBridgeConfig], AsyncIterator[TelegramIncomingUpdate]
                    ],
                    partial(poll_updates, sleep=sleep),
                )
            else:
                poller_fn = poller
            config_path = cfg.runtime.config_path
            watch_enabled = bool(watch_config) and config_path is not None

            async def handle_reload(reload: ConfigReload) -> None:
                refresh_commands()
                refresh_topics_scope()
                await set_command_menu(cfg)
                if state.transport_snapshot is not None:
                    new_snapshot = reload.settings.transports.telegram.model_dump()
                    changed = _diff_keys(state.transport_snapshot, new_snapshot)
                    if changed:
                        logger.warning(
                            "config.reload.transport_config_changed",
                            transport="telegram",
                            keys=changed,
                            restart_required=True,
                        )
                        state.transport_snapshot = new_snapshot
                if (
                    state.transport_id is not None
                    and reload.settings.transport != state.transport_id
                ):
                    logger.warning(
                        "config.reload.transport_changed",
                        old=state.transport_id,
                        new=reload.settings.transport,
                        restart_required=True,
                    )
                    state.transport_id = reload.settings.transport

            if watch_enabled and config_path is not None:

                async def run_config_watch() -> None:
                    await watch_config_changes(
                        config_path=config_path,
                        runtime=cfg.runtime,
                        default_engine_override=default_engine_override,
                        on_reload=handle_reload,
                    )

                tg.start_soon(run_config_watch)

            def wrap_on_thread_known(
                base_cb: Callable[[ResumeToken, anyio.Event], Awaitable[None]] | None,
                topic_key: tuple[int, int] | None,
                chat_session_key: tuple[int, int | None] | None,
            ) -> Callable[[ResumeToken, anyio.Event], Awaitable[None]] | None:
                if base_cb is None and topic_key is None and chat_session_key is None:
                    return None

                async def _wrapped(token: ResumeToken, done: anyio.Event) -> None:
                    if base_cb is not None:
                        await base_cb(token, done)
                    if state.topic_store is not None and topic_key is not None:
                        await state.topic_store.set_session_resume(
                            topic_key[0], topic_key[1], token
                        )
                    if (
                        state.chat_session_store is not None
                        and chat_session_key is not None
                    ):
                        await state.chat_session_store.set_session_resume(
                            chat_session_key[0], chat_session_key[1], token
                        )

                return _wrapped

            async def run_job(
                chat_id: int,
                user_msg_id: int,
                text: str,
                resume_token: ResumeToken | None,
                context: RunContext | None,
                thread_id: int | None = None,
                chat_session_key: tuple[int, int | None] | None = None,
                reply_ref: MessageRef | None = None,
                on_thread_known: Callable[[ResumeToken, anyio.Event], Awaitable[None]]
                | None = None,
                engine_override: EngineId | None = None,
                progress_ref: MessageRef | None = None,
            ) -> None:
                topic_key = (
                    (chat_id, thread_id)
                    if state.topic_store is not None
                    and thread_id is not None
                    and _topics_chat_allowed(
                        cfg, chat_id, scope_chat_ids=state.topics_chat_ids
                    )
                    else None
                )
                stateful_mode = topic_key is not None or chat_session_key is not None
                show_resume_line = should_show_resume_line(
                    show_resume_line=cfg.show_resume_line,
                    stateful_mode=stateful_mode,
                    context=context,
                )
                engine_for_overrides = (
                    resume_token.engine
                    if resume_token is not None
                    else engine_override
                    if engine_override is not None
                    else cfg.runtime.resolve_engine(
                        engine_override=None,
                        context=context,
                    )
                )
                overrides_thread_id = topic_key[1] if topic_key is not None else None
                run_options = await _resolve_engine_run_options(
                    chat_id,
                    overrides_thread_id,
                    engine_for_overrides,
                    chat_prefs=state.chat_prefs,
                    topic_store=state.topic_store,
                )
                await run_engine(
                    exec_cfg=cfg.exec_cfg,
                    runtime=cfg.runtime,
                    running_tasks=state.running_tasks,
                    chat_id=chat_id,
                    user_msg_id=user_msg_id,
                    text=text,
                    resume_token=resume_token,
                    context=context,
                    reply_ref=reply_ref,
                    on_thread_known=wrap_on_thread_known(
                        on_thread_known, topic_key, chat_session_key
                    ),
                    engine_override=engine_override,
                    thread_id=thread_id,
                    show_resume_line=show_resume_line,
                    progress_ref=progress_ref,
                    run_options=run_options,
                )

            async def run_thread_job(job: ThreadJob) -> None:
                await run_job(
                    cast(int, job.chat_id),
                    cast(int, job.user_msg_id),
                    job.text,
                    job.resume_token,
                    job.context,
                    cast(int | None, job.thread_id),
                    job.session_key,
                    None,
                    scheduler.note_thread_known,
                    None,
                    job.progress_ref,
                )

            scheduler = ThreadScheduler(task_group=tg, run_job=run_thread_job)

            def resolve_topic_key(
                msg: TelegramIncomingMessage,
            ) -> tuple[int, int] | None:
                if state.topic_store is None:
                    return None
                return _topic_key(msg, cfg, scope_chat_ids=state.topics_chat_ids)

            def _build_upload_prompt(base: str, annotation: str) -> str:
                if base and base.strip():
                    return f"{base}\n\n{annotation}"
                return annotation

            async def resolve_prompt_message(
                msg: TelegramIncomingMessage,
                text: str,
                ambient_context: RunContext | None,
            ) -> ResolvedMessage | None:
                reply = make_reply(cfg, msg)
                try:
                    resolved = cfg.runtime.resolve_message(
                        text=text,
                        reply_text=msg.reply_to_text,
                        ambient_context=ambient_context,
                        chat_id=msg.chat_id,
                    )
                except DirectiveError as exc:
                    await reply(text=f"error:\n{exc}")
                    return None
                topic_key = resolve_topic_key(msg)
                effective_context = ambient_context
                if (
                    state.topic_store is not None
                    and topic_key is not None
                    and resolved.context is not None
                    and resolved.context_source == "directives"
                ):
                    await state.topic_store.set_context(*topic_key, resolved.context)
                    await _maybe_rename_topic(
                        cfg,
                        state.topic_store,
                        chat_id=topic_key[0],
                        thread_id=topic_key[1],
                        context=resolved.context,
                    )
                    effective_context = resolved.context
                if (
                    state.topic_store is not None
                    and topic_key is not None
                    and effective_context is None
                    and resolved.context_source not in {"directives", "reply_ctx"}
                ):
                    chat_project = (
                        _topics_chat_project(cfg, msg.chat_id)
                        if cfg.topics.enabled
                        else None
                    )
                    await reply(
                        text="this topic isn't bound to a project yet.\n"
                        f"{_usage_ctx_set(chat_project=chat_project)} or "
                        f"{_usage_topic(chat_project=chat_project)}",
                    )
                    return None
                return resolved

            async def resolve_engine_defaults(
                *,
                explicit_engine: EngineId | None,
                context: RunContext | None,
                chat_id: int,
                topic_key: tuple[int, int] | None,
            ):
                return await resolve_engine_for_message(
                    runtime=cfg.runtime,
                    context=context,
                    explicit_engine=explicit_engine,
                    chat_id=chat_id,
                    topic_key=topic_key,
                    topic_store=state.topic_store,
                    chat_prefs=state.chat_prefs,
                )

            resume_resolver = ResumeResolver(
                cfg=cfg,
                task_group=tg,
                running_tasks=state.running_tasks,
                enqueue_resume=scheduler.enqueue_resume,
                topic_store=state.topic_store,
                chat_session_store=state.chat_session_store,
            )

            async def run_prompt_from_upload(
                msg: TelegramIncomingMessage,
                prompt_text: str,
                resolved: ResolvedMessage,
            ) -> None:
                chat_id = msg.chat_id
                user_msg_id = msg.message_id
                reply_id = msg.reply_to_message_id
                reply_ref = (
                    MessageRef(
                        channel_id=msg.chat_id,
                        message_id=msg.reply_to_message_id,
                        thread_id=msg.thread_id,
                    )
                    if msg.reply_to_message_id is not None
                    else None
                )
                resume_token = resolved.resume_token
                context = resolved.context
                chat_session_key = _chat_session_key(
                    msg, store=state.chat_session_store
                )
                topic_key = resolve_topic_key(msg)
                engine_resolution = await resolve_engine_defaults(
                    explicit_engine=resolved.engine_override,
                    context=context,
                    chat_id=chat_id,
                    topic_key=topic_key,
                )
                engine_override = engine_resolution.engine
                resume_decision = await resume_resolver.resolve(
                    resume_token=resume_token,
                    reply_id=reply_id,
                    chat_id=chat_id,
                    user_msg_id=user_msg_id,
                    thread_id=msg.thread_id,
                    chat_session_key=chat_session_key,
                    topic_key=topic_key,
                    engine_for_session=engine_resolution.engine,
                    prompt_text=prompt_text,
                )
                if resume_decision.handled_by_running_task:
                    return
                resume_token = resume_decision.resume_token
                if resume_token is None:
                    await run_job(
                        chat_id,
                        user_msg_id,
                        prompt_text,
                        None,
                        context,
                        msg.thread_id,
                        chat_session_key,
                        reply_ref,
                        scheduler.note_thread_known,
                        engine_override,
                    )
                    return
                progress_ref = await _send_queued_progress(
                    cfg,
                    chat_id=chat_id,
                    user_msg_id=user_msg_id,
                    thread_id=msg.thread_id,
                    resume_token=resume_token,
                    context=context,
                )
                await scheduler.enqueue_resume(
                    chat_id,
                    user_msg_id,
                    prompt_text,
                    resume_token,
                    context,
                    msg.thread_id,
                    chat_session_key,
                    progress_ref,
                )

            async def _dispatch_pending_prompt(pending: _PendingPrompt) -> None:
                msg = pending.msg
                chat_id = msg.chat_id
                user_msg_id = msg.message_id
                reply = make_reply(cfg, msg)
                try:
                    resolved = cfg.runtime.resolve_message(
                        text=pending.text,
                        reply_text=msg.reply_to_text,
                        ambient_context=pending.ambient_context,
                        chat_id=chat_id,
                    )
                except DirectiveError as exc:
                    await reply(text=f"error:\n{exc}")
                    return
                if pending.is_voice_transcribed:
                    resolved = ResolvedMessage(
                        prompt=f"(voice transcribed) {resolved.prompt}",
                        resume_token=resolved.resume_token,
                        engine_override=resolved.engine_override,
                        context=resolved.context,
                        context_source=resolved.context_source,
                    )

                prompt_text = resolved.prompt
                if pending.forwards:
                    forwarded = [
                        text
                        for _, text in sorted(
                            pending.forwards,
                            key=lambda item: item[0],
                        )
                    ]
                    prompt_text = _format_forwarded_prompt(
                        forwarded,
                        prompt_text,
                    )

                resume_token = resolved.resume_token
                context = resolved.context
                engine_resolution = await resolve_engine_defaults(
                    explicit_engine=resolved.engine_override,
                    context=context,
                    chat_id=chat_id,
                    topic_key=pending.topic_key,
                )
                engine_override = engine_resolution.engine
                effective_context = pending.ambient_context
                if (
                    state.topic_store is not None
                    and pending.topic_key is not None
                    and resolved.context is not None
                    and resolved.context_source == "directives"
                ):
                    await state.topic_store.set_context(
                        *pending.topic_key, resolved.context
                    )
                    await _maybe_rename_topic(
                        cfg,
                        state.topic_store,
                        chat_id=pending.topic_key[0],
                        thread_id=pending.topic_key[1],
                        context=resolved.context,
                    )
                    effective_context = resolved.context
                if (
                    state.topic_store is not None
                    and pending.topic_key is not None
                    and effective_context is None
                    and resolved.context_source not in {"directives", "reply_ctx"}
                ):
                    await reply(
                        text="this topic isn't bound to a project yet.\n"
                        f"{_usage_ctx_set(chat_project=pending.chat_project)} or "
                        f"{_usage_topic(chat_project=pending.chat_project)}",
                    )
                    return
                resume_decision = await resume_resolver.resolve(
                    resume_token=resume_token,
                    reply_id=pending.reply_id,
                    chat_id=chat_id,
                    user_msg_id=user_msg_id,
                    thread_id=msg.thread_id,
                    chat_session_key=pending.chat_session_key,
                    topic_key=pending.topic_key,
                    engine_for_session=engine_resolution.engine,
                    prompt_text=prompt_text,
                )
                if resume_decision.handled_by_running_task:
                    return
                resume_token = resume_decision.resume_token

                if resume_token is None:
                    tg.start_soon(
                        run_job,
                        chat_id,
                        user_msg_id,
                        prompt_text,
                        None,
                        context,
                        msg.thread_id,
                        pending.chat_session_key,
                        pending.reply_ref,
                        scheduler.note_thread_known,
                        engine_override,
                    )
                    return
                progress_ref = await _send_queued_progress(
                    cfg,
                    chat_id=chat_id,
                    user_msg_id=user_msg_id,
                    thread_id=msg.thread_id,
                    resume_token=resume_token,
                    context=context,
                )
                await scheduler.enqueue_resume(
                    chat_id,
                    user_msg_id,
                    prompt_text,
                    resume_token,
                    context,
                    msg.thread_id,
                    pending.chat_session_key,
                    progress_ref,
                )

            forward_coalescer = ForwardCoalescer(
                task_group=tg,
                debounce_s=state.forward_coalesce_s,
                sleep=sleep,
                dispatch=_dispatch_pending_prompt,
                pending=state.pending_prompts,
            )

            async def handle_prompt_upload(
                msg: TelegramIncomingMessage,
                caption_text: str,
                ambient_context: RunContext | None,
                topic_store: TopicStateStore | None,
            ) -> None:
                resolved = await resolve_prompt_message(
                    msg,
                    caption_text,
                    ambient_context,
                )
                if resolved is None:
                    return
                saved = await save_file_put(
                    cfg,
                    msg,
                    "",
                    resolved.context,
                    topic_store,
                )
                if saved is None:
                    return
                annotation = f"[uploaded file: {saved.rel_path.as_posix()}]"
                prompt = _build_upload_prompt(resolved.prompt, annotation)
                await run_prompt_from_upload(msg, prompt, resolved)

            media_group_buffer = MediaGroupBuffer(
                task_group=tg,
                debounce_s=state.media_group_debounce_s,
                sleep=sleep,
                cfg=cfg,
                chat_prefs=state.chat_prefs,
                topic_store=state.topic_store,
                bot_username=state.bot_username,
                command_ids=lambda: state.command_ids,
                reserved_chat_commands=state.reserved_chat_commands,
                groups=state.media_groups,
                run_prompt_from_upload=run_prompt_from_upload,
                resolve_prompt_message=resolve_prompt_message,
            )

            async def build_message_context(
                msg: TelegramIncomingMessage,
            ) -> TelegramMsgContext:
                chat_id = msg.chat_id
                reply_id = msg.reply_to_message_id
                reply_ref = (
                    MessageRef(channel_id=chat_id, message_id=reply_id)
                    if reply_id is not None
                    else None
                )
                topic_key = resolve_topic_key(msg)
                chat_session_key = _chat_session_key(
                    msg, store=state.chat_session_store
                )
                stateful_mode = topic_key is not None or chat_session_key is not None
                chat_project = (
                    _topics_chat_project(cfg, chat_id) if cfg.topics.enabled else None
                )
                bound_context = (
                    await state.topic_store.get_context(*topic_key)
                    if state.topic_store is not None and topic_key is not None
                    else None
                )
                chat_bound_context = None
                if state.chat_prefs is not None:
                    chat_bound_context = await state.chat_prefs.get_context(chat_id)
                if bound_context is not None:
                    ambient_context = _merge_topic_context(
                        chat_project=chat_project, bound=bound_context
                    )
                elif chat_bound_context is not None:
                    ambient_context = chat_bound_context
                else:
                    ambient_context = _merge_topic_context(
                        chat_project=chat_project, bound=None
                    )
                return TelegramMsgContext(
                    chat_id=chat_id,
                    thread_id=msg.thread_id,
                    reply_id=reply_id,
                    reply_ref=reply_ref,
                    topic_key=topic_key,
                    chat_session_key=chat_session_key,
                    stateful_mode=stateful_mode,
                    chat_project=chat_project,
                    ambient_context=ambient_context,
                )

            async def route_message(msg: TelegramIncomingMessage) -> None:
                reply = make_reply(cfg, msg)
                text = msg.text
                is_voice_transcribed = False
                is_forward_candidate = (
                    _is_forwarded(msg.raw)
                    and msg.document is None
                    and msg.voice is None
                    and msg.media_group_id is None
                )
                if is_forward_candidate:
                    forward_coalescer.attach_forward(msg)
                    return
                forward_key = _forward_key(msg)
                if (
                    cfg.files.enabled
                    and msg.document is not None
                    and msg.media_group_id is not None
                ):
                    media_group_buffer.add(msg)
                    return
                ctx = await build_message_context(msg)
                chat_id = ctx.chat_id
                reply_id = ctx.reply_id
                reply_ref = ctx.reply_ref
                topic_key = ctx.topic_key
                chat_session_key = ctx.chat_session_key
                stateful_mode = ctx.stateful_mode
                chat_project = ctx.chat_project
                ambient_context = ctx.ambient_context

                if is_cancel_command(text):
                    tg.start_soon(
                        handle_cancel, cfg, msg, state.running_tasks, scheduler
                    )
                    return

                command_id, args_text = parse_slash_command(text)
                if command_id == "new":
                    forward_coalescer.cancel(forward_key)
                    if state.topic_store is not None and topic_key is not None:
                        tg.start_soon(
                            partial(
                                handle_new_command,
                                cfg,
                                msg,
                                state.topic_store,
                                resolved_scope=state.resolved_topics_scope,
                                scope_chat_ids=state.topics_chat_ids,
                            )
                        )
                        return
                    if state.chat_session_store is not None:
                        tg.start_soon(
                            handle_chat_new_command,
                            cfg,
                            msg,
                            state.chat_session_store,
                            chat_session_key,
                        )
                        return
                    if state.topic_store is not None:
                        tg.start_soon(
                            partial(
                                handle_new_command,
                                cfg,
                                msg,
                                state.topic_store,
                                resolved_scope=state.resolved_topics_scope,
                                scope_chat_ids=state.topics_chat_ids,
                            )
                        )
                        return
                if command_id is not None and _dispatch_builtin_command(
                    ctx=TelegramCommandContext(
                        cfg=cfg,
                        msg=msg,
                        args_text=args_text,
                        ambient_context=ambient_context,
                        topic_store=state.topic_store,
                        chat_prefs=state.chat_prefs,
                        resolved_scope=state.resolved_topics_scope,
                        scope_chat_ids=state.topics_chat_ids,
                        reply=reply,
                        task_group=tg,
                    ),
                    command_id=command_id,
                ):
                    return

                trigger_mode = await resolve_trigger_mode(
                    chat_id=chat_id,
                    thread_id=msg.thread_id,
                    chat_prefs=state.chat_prefs,
                    topic_store=state.topic_store,
                )
                if trigger_mode == "mentions" and not should_trigger_run(
                    msg,
                    bot_username=state.bot_username,
                    runtime=cfg.runtime,
                    command_ids=state.command_ids,
                    reserved_chat_commands=state.reserved_chat_commands,
                ):
                    return

                if msg.voice is not None:
                    text = await transcribe_voice(
                        bot=cfg.bot,
                        msg=msg,
                        enabled=cfg.voice_transcription,
                        model=cfg.voice_transcription_model,
                        max_bytes=cfg.voice_max_bytes,
                        reply=reply,
                        base_url=cfg.voice_transcription_base_url,
                        api_key=cfg.voice_transcription_api_key,
                    )
                    if text is None:
                        return
                    is_voice_transcribed = True
                if msg.document is not None:
                    if cfg.files.enabled and cfg.files.auto_put:
                        caption_text = text.strip()
                        if cfg.files.auto_put_mode == "prompt" and caption_text:
                            tg.start_soon(
                                handle_prompt_upload,
                                msg,
                                caption_text,
                                ambient_context,
                                state.topic_store,
                            )
                        elif not caption_text:
                            tg.start_soon(
                                handle_file_put_default,
                                cfg,
                                msg,
                                ambient_context,
                                state.topic_store,
                            )
                        else:
                            tg.start_soon(
                                partial(reply, text=FILE_PUT_USAGE),
                            )
                    elif cfg.files.enabled:
                        tg.start_soon(
                            partial(reply, text=FILE_PUT_USAGE),
                        )
                    return
                if command_id is not None and command_id not in state.reserved_commands:
                    if command_id not in state.command_ids:
                        refresh_commands()
                    if command_id in state.command_ids:
                        engine_resolution = await resolve_engine_defaults(
                            explicit_engine=None,
                            context=ambient_context,
                            chat_id=chat_id,
                            topic_key=topic_key,
                        )
                        default_engine_override = (
                            engine_resolution.engine
                            if engine_resolution.source
                            in {"directive", "topic_default", "chat_default"}
                            else None
                        )
                        overrides_thread_id = (
                            topic_key[1] if topic_key is not None else None
                        )
                        engine_overrides_resolver = partial(
                            _resolve_engine_run_options,
                            chat_id,
                            overrides_thread_id,
                            chat_prefs=state.chat_prefs,
                            topic_store=state.topic_store,
                        )
                        tg.start_soon(
                            dispatch_command,
                            cfg,
                            msg,
                            text,
                            command_id,
                            args_text,
                            state.running_tasks,
                            scheduler,
                            wrap_on_thread_known(
                                scheduler.note_thread_known,
                                topic_key,
                                chat_session_key,
                            ),
                            stateful_mode,
                            default_engine_override,
                            engine_overrides_resolver,
                        )
                        return

                pending = _PendingPrompt(
                    msg=msg,
                    text=text,
                    ambient_context=ambient_context,
                    chat_project=chat_project,
                    topic_key=topic_key,
                    chat_session_key=chat_session_key,
                    reply_ref=reply_ref,
                    reply_id=reply_id,
                    is_voice_transcribed=is_voice_transcribed,
                    forwards=[],
                )
                if reply_id is not None and state.running_tasks.get(
                    MessageRef(channel_id=chat_id, message_id=reply_id)
                ):
                    logger.debug(
                        "forward.prompt.bypass",
                        chat_id=chat_id,
                        thread_id=msg.thread_id,
                        sender_id=msg.sender_id,
                        message_id=msg.message_id,
                        reason="reply_resume",
                    )
                    tg.start_soon(_dispatch_pending_prompt, pending)
                    return
                forward_coalescer.schedule(pending)

            allowed_user_ids = set(cfg.allowed_user_ids)

            async def route_update(update: TelegramIncomingUpdate) -> None:
                if allowed_user_ids:
                    sender_id = update.sender_id
                    if sender_id is None or sender_id not in allowed_user_ids:
                        logger.debug(
                            "update.ignored",
                            reason="sender_not_allowed",
                            chat_id=update.chat_id,
                            sender_id=sender_id,
                        )
                        return
                if isinstance(update, TelegramCallbackQuery):
                    if update.data == CANCEL_CALLBACK_DATA:
                        tg.start_soon(
                            handle_callback_cancel,
                            cfg,
                            update,
                            state.running_tasks,
                            scheduler,
                        )
                    else:
                        tg.start_soon(
                            cfg.bot.answer_callback_query,
                            update.callback_query_id,
                        )
                    return
                await route_message(update)

            async for update in poller_fn(cfg):
                await route_update(update)
    finally:
        await cfg.exec_cfg.transport.close()
