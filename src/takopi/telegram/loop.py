from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

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
    forwards: list[TelegramIncomingMessage]
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


def _format_timestamp(date: int | None) -> str:
    if date is None:
        return "time=?"
    return datetime.fromtimestamp(date, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _format_sender(msg: TelegramIncomingMessage) -> str:
    parts = [part for part in (msg.sender_first_name, msg.sender_last_name) if part]
    if parts:
        return " ".join(parts)
    if msg.sender_username:
        return f"@{msg.sender_username}"
    if msg.sender_id is not None:
        return f"user:{msg.sender_id}"
    return "unknown"


def _format_prompt_header(
    msg: TelegramIncomingMessage,
    *,
    prompt_mode: Literal["default", "party"] = "default",
) -> str:
    if prompt_mode != "party" or msg.thread_id is None:
        return ""
    timestamp = _format_timestamp(msg.date)
    sender = _format_sender(msg)
    return f"[{timestamp}] {sender}:"


def _format_prompt_line(
    msg: TelegramIncomingMessage,
    text: str,
    *,
    prompt_mode: Literal["default", "party"] = "default",
) -> str:
    header = _format_prompt_header(msg, prompt_mode=prompt_mode)
    if not header:
        return text
    if text.strip():
        return f"{header} {text}"
    return header


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
        pending.forwards.append(msg)
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
            message_date=msg.date,
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
        media_group_id = msg.media_group_id
        if media_group_id is None:
            return
        key = (msg.chat_id, media_group_id)
        state = self._groups.get(key)
        if state is None:
            state = _MediaGroupState(messages=[])
            self._groups[key] = state
        state.messages.append(msg)
        state.token += 1
        token = state.token
        self._task_group.start_soon(self._flush_media_group, key, token)

    async def _flush_media_group(
        self,
        key: tuple[int, str],
        token: int,
    ) -> None:
        await self._sleep(self._debounce_s)
        state = self._groups.get(key)
        if state is None or token != state.token:
            return
        self._groups.pop(key, None)
        await self._dispatch(state.messages)

    async def _dispatch(self, messages: list[TelegramIncomingMessage]) -> None:
        prompt_message = None
        prompt_media: TelegramIncomingMessage | None = None
        for msg in messages:
            if msg.caption:
                prompt_message = msg
                prompt_media = msg
                break
            if msg.document is None:
                prompt_media = msg
        if prompt_message is None:
            prompt_message = prompt_media
        if prompt_message is None:
            return
        caption_text = (prompt_message.caption or "").strip()
        if prompt_media is not None and caption_text:
            reply = make_reply(self._cfg, prompt_media)
            await reply(text=FILE_PUT_USAGE)
            return
        resolved = await self._resolve_prompt_message(
            prompt_message,
            caption_text,
            None,
        )
        if resolved is None:
            return
        annotation = ""
        if prompt_media is not None and prompt_media.document is not None:
            saved = await save_file_put(
                self._cfg,
                prompt_media,
                "",
                resolved.context,
                self._topic_store,
            )
            if saved is None:
                return
            annotation = f"[uploaded file: {saved.rel_path.as_posix()}]"
        prompt = _build_upload_prompt(resolved.prompt, annotation)
        chat_id = prompt_message.chat_id
        user_msg_id = prompt_message.message_id
        reply_id = prompt_message.reply_to_message_id
        reply_ref = (
            MessageRef(
                channel_id=chat_id,
                message_id=reply_id,
                thread_id=prompt_message.thread_id,
            )
            if reply_id is not None
            else None
        )
        prompt = _format_prompt_line(
            prompt_message,
            prompt,
            prompt_mode=self._cfg.topics.prompt_mode,
        )
        resume_token = resolved.resume_token
        context = resolved.context
        chat_session_key = _chat_session_key(
            prompt_message, store=self._chat_session_store
        )
        topic_key = (
            _topic_key(prompt_message, self._cfg, scope_chat_ids=self._topics_chat_ids)
            if self._topic_store is not None
            else None
        )
        engine_resolution = await self._resolve_engine_defaults(
            explicit_engine=resolved.engine_override,
            context=context,
            chat_id=chat_id,
            topic_key=topic_key,
        )
        engine_override = engine_resolution.engine
        resume_decision = await self._resume_resolver.resolve(
            resume_token=resume_token,
            reply_id=reply_id,
            chat_id=chat_id,
            user_msg_id=user_msg_id,
            thread_id=prompt_message.thread_id,
            chat_session_key=chat_session_key,
            topic_key=topic_key,
            engine_for_session=engine_resolution.engine,
            prompt_text=prompt,
        )
        if resume_decision.handled_by_running_task:
            return
        resume_token = resume_decision.resume_token
        if resume_token is None:
            await run_job(
                chat_id,
                user_msg_id,
                prompt,
                None,
                context,
                prompt_message.thread_id,
                chat_session_key,
                reply_ref,
                self._scheduler.note_thread_known,
                engine_override,
            )
            return
        progress_ref = await _send_queued_progress(
            self._cfg,
            chat_id=chat_id,
            user_msg_id=user_msg_id,
            thread_id=prompt_message.thread_id,
            resume_token=resume_token,
            context=context,
        )
        await self._scheduler.enqueue_resume(
            chat_id,
            user_msg_id,
            prompt,
            resume_token,
            context,
            prompt_message.thread_id,
            chat_session_key,
            progress_ref,
        )


def _build_upload_prompt(prompt: str, annotation: str) -> str:
    if not annotation:
        return prompt
    if not prompt.strip():
        return annotation
    return f"{prompt}\n\n{annotation}"


async def send_with_resume(
    cfg: TelegramBridgeConfig,
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
    running_task: object,
    chat_id: int,
    user_msg_id: int,
    thread_id: int | None,
    chat_session_key: tuple[int, int | None] | None,
    prompt_text: str,
) -> None:
    reply_ref = MessageRef(channel_id=chat_id, message_id=user_msg_id, thread_id=thread_id)
    sent = await send_plain(cfg, chat_id=chat_id, text="resuming...", thread_id=thread_id)
    if sent is None:
        await enqueue_resume(
            chat_id,
            user_msg_id,
            prompt_text,
            ResumeToken(ref=reply_ref),
            None,
            thread_id,
            chat_session_key,
            None,
        )
        return
    resume_ref = MessageRef(
        channel_id=chat_id,
        message_id=sent.message_id,
        thread_id=thread_id,
    )
    await enqueue_resume(
        chat_id,
        user_msg_id,
        prompt_text,
        ResumeToken(ref=resume_ref),
        None,
        thread_id,
        chat_session_key,
        reply_ref,
    )


async def run_main_loop(
    cfg: TelegramBridgeConfig,
    *,
    poller_fn: Callable[[TelegramBridgeConfig], AsyncIterator[TelegramIncomingUpdate]] = poll_updates,
) -> None:
    logger.info("telegram.loop.starting", chat_id=cfg.chat_id)
    scheduler = ThreadScheduler(max_concurrency=cfg.max_concurrency)
    scheduler_task: anyio.abc.TaskStatus[ThreadScheduler] | None = None
    if cfg.thread_pool:
        scheduler_task = await anyio.create_task_group().start(  # noqa: SIM117
            scheduler.start
        )
    running_tasks: dict[MessageRef, ThreadJob] = {}
    stream_connections: dict[MessageRef, anyio.abc.SocketStream] = {}
    attach_streams: dict[MessageRef, anyio.abc.SocketStream] = {}
    stream_blocks: dict[MessageRef, tuple[bytes, bytes]] = {}

    state = TelegramLoopState(
        running_tasks=running_tasks,
        pending_prompts={},
        media_groups={},
        command_ids=set(),
        reserved_commands=set(),
        reserved_chat_commands=set(),
        transport_snapshot=None,
        topic_store=None,
        chat_session_store=None,
        chat_prefs=None,
        resolved_topics_scope=None,
        topics_chat_ids=frozenset(),
        bot_username=None,
        forward_coalesce_s=cfg.forward_coalesce_s,
        media_group_debounce_s=cfg.media_group_debounce_s,
        transport_id=cfg.transport_id,
    )

    def refresh_commands() -> None:
        state.command_ids = set(list_command_ids())

    def refresh_reserved_commands() -> None:
        state.reserved_commands = get_reserved_commands()
        state.reserved_chat_commands = set(RESERVED_CHAT_COMMANDS)
        if cfg.topics.enabled:
            state.reserved_chat_commands |= _TOPICS_COMMANDS

    def refresh_topics_scope() -> None:
        if cfg.topics.enabled:
            (
                state.resolved_topics_scope,
                state.topics_chat_ids,
            ) = _resolve_topics_scope(cfg)
        else:
            state.resolved_topics_scope = None
            state.topics_chat_ids = frozenset()

    def update_command_menu() -> None:
        if not cfg.command_menu:
            return
        set_command_menu(cfg, state.command_ids, state.reserved_chat_commands)

    def update_transport_snapshot() -> None:
        state.transport_snapshot = cfg.snapshot()

    async def _send_queued_progress(
        cfg: TelegramBridgeConfig,
        *,
        chat_id: int,
        user_msg_id: int,
        thread_id: int | None,
        resume_token: ResumeToken,
        context: RunContext | None,
    ) -> MessageRef:
        reply_ref = MessageRef(
            channel_id=chat_id,
            message_id=user_msg_id,
            thread_id=thread_id,
        )
        progress = ProgressTracker(
            on_update=partial(
                _enqueue_stream_edit,
                reply_ref=reply_ref,
            ),
            on_replace=partial(
                _send_replace_message,
                cfg,
                reply_ref=reply_ref,
            ),
            on_done=partial(
                _send_replace_message,
                cfg,
                reply_ref=reply_ref,
                use_reply=False,
            ),
        )
        try:
            await cfg.exec_cfg.runtime.run_resume(
                resume_token,
                progress,
                context=context,
                transport=cfg.transport_id,
            )
        except Exception:
            progress.close()
            raise
        return reply_ref

    async def _send_replace_message(
        cfg: TelegramBridgeConfig,
        message: str,
        *,
        reply_ref: MessageRef,
        use_reply: bool = True,
    ) -> None:
        rendered_text, entities = cfg.exec_cfg.runtime.render(message)
        message = ResolvedMessage(rendered_text, entities=entities)
        sent = await cfg.exec_cfg.transport.send(
            channel_id=reply_ref.channel_id,
            message=message,
            thread_id=reply_ref.thread_id,
            reply_to=reply_ref if use_reply else None,
        )
        if sent is None:
            return
        ref = MessageRef(
            channel_id=reply_ref.channel_id,
            message_id=sent.message_id,
            thread_id=reply_ref.thread_id,
        )
        if use_reply:
            state.running_tasks[ref] = state.running_tasks.pop(reply_ref)
        await cfg.exec_cfg.transport.delete(reply_ref)

    def _enqueue_stream_edit(
        reply_ref: MessageRef,
        message: str,
    ) -> None:
        rendered_text, entities = cfg.exec_cfg.runtime.render(message)
        send_options = SendOptions(entities=entities, thread_id=reply_ref.thread_id)
        scheduler.submit(
            ThreadJob(
                priority=2,
                name="telegram.edit",
                fn=partial(
                    cfg.exec_cfg.transport.edit,
                    reply_ref,
                    rendered_text,
                    send_options,
                ),
            )
        )

    def _resolve_context(msg: TelegramIncomingMessage) -> tuple[RunContext | None, str]:
        ambient_context = None
        topic_key = resolve_topic_key(msg)
        if topic_key is not None and state.topic_store is not None:
            bound = state.topic_store.get_context_sync(*topic_key)
            if bound is not None:
                return bound, "topic"
        if state.chat_prefs is not None:
            bound = state.chat_prefs.get_context_sync(msg.chat_id)
            if bound is not None:
                return bound, "chat"
        return ambient_context, "default"

    async def refresh_config() -> None:
        config_path = cfg.config_path
        if config_path is None:
            raise ConfigError("config path is required")
        if not config_path.exists():
            raise ConfigError(f"config path {config_path} does not exist")
        new_settings = await cfg.exec_cfg.runtime.load_settings(config_path)
        if not isinstance(new_settings, TelegramTransportSettings):
            raise ConfigError("invalid telegram config")
        cfg.apply_settings(new_settings)
        state.forward_coalesce_s = cfg.forward_coalesce_s
        state.media_group_debounce_s = cfg.media_group_debounce_s
        state.transport_id = cfg.transport_id
        refresh_commands()
        refresh_reserved_commands()
        refresh_topics_scope()
        update_command_menu()
        update_transport_snapshot()

    async def resolve_prompt_message(
        msg: TelegramIncomingMessage,
        text: str,
        ambient_context: RunContext | None,
    ) -> ResolvedMessage | None:
        if not text.strip() and msg.document is None and msg.voice is None:
            return None
        reply_ref: MessageRef | None = None
        reply_text: str | None = None
        reply_id = msg.reply_to_message_id
        if reply_id is not None and msg.reply_to_text:
            reply_ref = MessageRef(
                channel_id=msg.chat_id,
                message_id=reply_id,
                thread_id=msg.thread_id,
            )
            reply_text = msg.reply_to_text
        prompt = _format_prompt_line(msg, text, prompt_mode=cfg.topics.prompt_mode)
        if not prompt.strip():
            return None
        context, source = _resolve_context(msg)
        resolved = cfg.runtime.resolve_message(
            text=prompt,
            reply_text=reply_text,
            ambient_context=ambient_context,
            chat_id=msg.chat_id,
        )
        return ResolvedMessage(
            prompt=resolved.prompt,
            resume_token=resolved.resume_token,
            engine_override=resolved.engine_override,
            context=resolved.context,
            context_source=source,
        )

    async def _handle_msgspec_prompt(
        msg: TelegramIncomingMessage,
        text: str,
        ambient_context: RunContext | None,
        scheduler: ThreadScheduler,
        on_thread_known: Callable[[int, int], None],
        base_cb: Callable[[ThreadJob], None] | None,
    ) -> None:
        resolved = await resolve_prompt_message(msg, text, ambient_context)
        if resolved is None:
            return
        prompt_text = resolved.prompt
        if msg.document is not None and cfg.files.enabled:
            msg = await save_file_put(cfg, msg, text, resolved.context, state.topic_store)
            if msg is None:
                return
            prompt_text = _format_prompt_line(
                msg,
                prompt_text,
                prompt_mode=cfg.topics.prompt_mode,
            )
        chat_id = msg.chat_id
        user_msg_id = msg.message_id
        reply_id = msg.reply_to_message_id
        reply_ref = (
            MessageRef(
                channel_id=chat_id,
                message_id=reply_id,
                thread_id=msg.thread_id,
            )
            if reply_id is not None
            else None
        )
        resume_token = resolved.resume_token
        context = resolved.context
        chat_session_key = _chat_session_key(msg, store=state.chat_session_store)
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

    def _log_router(fallback: Callable[[TelegramIncomingMessage], object]) -> Callable[[TelegramIncomingMessage], object]:
        if not cfg.log_json:
            return fallback

        def logger_for_msg(msg: TelegramIncomingMessage) -> object:
            ts = _format_timestamp(msg.date)
            info = {
                "timestamp": ts,
                "message_id": msg.message_id,
                "chat_id": msg.chat_id,
                "thread_id": msg.thread_id,
                "sender_id": msg.sender_id,
                "sender": _format_sender(msg),
                "text": msg.text,
                "caption": msg.caption,
                "reply_to": msg.reply_to_message_id,
                "document": msg.document,
                "voice": msg.voice,
                "video": msg.video,
                "photo": msg.photo,
                "sticker": msg.sticker,
                "media_group_id": msg.media_group_id,
                "is_topic_message": msg.is_topic_message,
                "is_automatic_forward": msg.is_automatic_forward,
            }
            if msg.document is not None:
                info["document_name"] = msg.document.file_name
            if msg.voice is not None:
                info["voice_duration"] = msg.voice.duration
            if msg.reply_to_message_id is not None:
                info["reply_text"] = msg.reply_to_text
            if msg.raw is not None:
                info["raw"] = msg.raw
            logger.info("telegram.message", **info)
            return fallback(msg)

        return logger_for_msg

    def _topic_router(fallback: Callable[[TelegramIncomingMessage], object]) -> Callable[[TelegramIncomingMessage], object]:
        def route(msg: TelegramIncomingMessage) -> object:
            if _topic_key(msg, cfg, scope_chat_ids=state.topics_chat_ids) is None:
                return fallback(msg)
            return topic_router(msg)

        return route

    def _dispatch_message(msg: TelegramIncomingMessage) -> object:
        return (dispatch_msgspec if cfg.msgspec_enabled else dispatch)(msg)

    def dispatch_msgspec(msg: TelegramIncomingMessage) -> object:
        if not msg.text and not msg.document and not msg.voice:
            return None
        if msg.raw is not None:
            if state.topic_store is not None and msg.thread_id is not None:
                state.topic_store.mark_thread_seen(msg.chat_id, msg.thread_id)
        return schedule_message(
            msg,
            msg.text or "",
            msg.reply_to_message_id,
            msg.reply_to_text,
        )

    def dispatch(msg: TelegramIncomingMessage) -> object:
        if msg.raw is not None and msg.thread_id is not None:
            if state.topic_store is not None:
                state.topic_store.mark_thread_seen(msg.chat_id, msg.thread_id)
        if cfg.files.enabled and msg.document is not None:
            return schedule_message(
                msg,
                msg.caption or "",
                msg.reply_to_message_id,
                msg.reply_to_text,
                msg.document,
            )
        return schedule_message(
            msg,
            msg.text or "",
            msg.reply_to_message_id,
            msg.reply_to_text,
        )

    async def schedule_message(
        msg: TelegramIncomingMessage,
        text: str,
        reply_id: int | None,
        reply_text: str | None,
        document: object | None = None,
    ) -> None:
        if msg.raw is None:
            if cfg.msgspec_enabled:
                await _handle_msgspec_prompt(
                    msg,
                    text,
                    None,
                    scheduler,
                    scheduler.note_thread_known,
                    None,
                )
                return
            await handle_new_prompt(
                msg,
                text,
                reply_id,
                reply_text,
                document=document,
            )
            return
        await _handle_prompt(
            msg,
            text,
            None,
            scheduler,
            scheduler.note_thread_known,
        )

    async def handle_new_prompt(
        msg: TelegramIncomingMessage,
        text: str,
        reply_id: int | None,
        reply_text: str | None,
        document: object | None = None,
    ) -> None:
        is_voice_transcribed = False
        ambient_context = RunContext(project=cfg.default_project, branch=None)
        if msg.voice is not None:
            text = await transcribe_voice(
                bot=cfg.bot,
                msg=msg,
                enabled=cfg.voice_transcription,
                model=cfg.voice_transcription_model,
                max_bytes=cfg.voice_max_bytes,
                reply=make_reply(cfg, msg),
                base_url=cfg.voice_transcription_base_url,
                api_key=cfg.voice_transcription_api_key,
            )
            if text is None:
                return
            is_voice_transcribed = True
        if document is not None and cfg.files.enabled:
            if isinstance(document, dict):
                await _handle_file_put_raw(
                    cfg,
                    msg,
                    document,
                    ambient_context,
                )
                return
            saved = await save_file_put(cfg, msg, text, ambient_context, None)
            if saved is None:
                return
            annotation = f"[uploaded file: {saved.rel_path.as_posix()}]"
            text = _build_upload_prompt(text, annotation)
        prompt_text = _format_prompt_line(
            msg,
            text,
            prompt_mode=cfg.topics.prompt_mode,
        )
        if not prompt_text.strip():
            return
        reply = make_reply(cfg, msg)
        try:
            resolved = cfg.runtime.resolve_message(
                text=prompt_text,
                reply_text=reply_text,
                ambient_context=ambient_context,
                chat_id=msg.chat_id,
            )
        except DirectiveError as exc:
            await reply(text=f"error:\n{exc}")
            return
        prompt_text = resolved.prompt
        if msg.document is None and msg.voice is None:
            prompt_text = _format_prompt_line(
                msg,
                prompt_text,
                prompt_mode=cfg.topics.prompt_mode,
            )
        if msg.document is None and msg.voice is None and msg.thread_id is not None:
            prompt_text = _format_forwarded_prompt(
                [prompt_text],
                "",
            )
        if msg.thread_id is not None and msg.sender_id is not None:
            pending = _PendingPrompt(
                msg=msg,
                text=prompt_text,
                ambient_context=ambient_context,
                chat_project=None,
                topic_key=None,
                chat_session_key=None,
                reply_ref=None,
                reply_id=None,
                is_voice_transcribed=is_voice_transcribed,
                forwards=[],
            )
            forward_coalescer.schedule(pending)
            return
        await run_job(
            msg.chat_id,
            msg.message_id,
            prompt_text,
            None,
            resolved.context,
            msg.thread_id,
            None,
            None,
            scheduler.note_thread_known,
            resolved.engine_override,
        )

    async def _handle_prompt(
        msg: TelegramIncomingMessage,
        text: str,
        ambient_context: RunContext | None,
        scheduler: ThreadScheduler,
        on_thread_known: Callable[[int, int], None],
    ) -> None:
        if msg.raw is not None and msg.thread_id is not None:
            if state.topic_store is not None:
                state.topic_store.mark_thread_seen(msg.chat_id, msg.thread_id)
        resolved = await resolve_prompt_message(msg, text, ambient_context)
        if resolved is None:
            return
        resume_token = resolved.resume_token
        prompt_text = resolved.prompt
        if msg.document is not None and cfg.files.enabled:
            msg = await save_file_put(cfg, msg, text, resolved.context, state.topic_store)
            if msg is None:
                return
            prompt_text = _format_prompt_line(
                msg,
                prompt_text,
                prompt_mode=cfg.topics.prompt_mode,
            )
        chat_id = msg.chat_id
        user_msg_id = msg.message_id
        reply_id = msg.reply_to_message_id
        reply_ref = (
            MessageRef(
                channel_id=chat_id,
                message_id=reply_id,
                thread_id=msg.thread_id,
            )
            if reply_id is not None
            else None
        )
        context = resolved.context
        engine_resolution = await resolve_engine_defaults(
            explicit_engine=resolved.engine_override,
            context=context,
            chat_id=chat_id,
            topic_key=resolve_topic_key(msg),
        )
        engine_override = engine_resolution.engine
        resume_decision = await resume_resolver.resolve(
            resume_token=resume_token,
            reply_id=reply_id,
            chat_id=chat_id,
            user_msg_id=user_msg_id,
            thread_id=msg.thread_id,
            chat_session_key=_chat_session_key(msg, store=state.chat_session_store),
            topic_key=resolve_topic_key(msg),
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
                _chat_session_key(msg, store=state.chat_session_store),
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
            _chat_session_key(msg, store=state.chat_session_store),
            progress_ref,
        )

    def wrap_on_thread_known(
        on_thread_known: Callable[[int, int], None],
        topic_key: tuple[int, int] | None,
        chat_session_key: tuple[int, int | None] | None,
    ) -> Callable[[int, int], None]:
        def wrapped(chat_id: int, thread_id: int) -> None:
            on_thread_known(chat_id, thread_id)
            if state.topic_store is not None and topic_key is not None:
                state.topic_store.mark_thread_seen(chat_id, thread_id)
            if chat_session_key is not None and thread_id is None:
                state.chat_session_store.mark_thread_seen(chat_id, chat_session_key[1])

        return wrapped

    def should_rename_topic(
        msg: TelegramIncomingMessage,
        context: RunContext,
        context_source: str,
    ) -> bool:
        if msg.thread_id is None:
            return False
        if not cfg.topics.enabled:
            return False
        return context_source == "directives"

    def resolve_topic_key(
        msg: TelegramIncomingMessage,
    ) -> tuple[int, int] | None:
        if state.topic_store is None:
            return None
        return _topic_key(msg, cfg, scope_chat_ids=state.topics_chat_ids)

    async def run_job(
        chat_id: int,
        user_msg_id: int,
        prompt_text: str,
        resume_token: ResumeToken | None,
        context: RunContext | None,
        thread_id: int | None,
        chat_session_key: tuple[int, int | None] | None,
        reply_ref: MessageRef | None,
        on_thread_known: Callable[[int, int], None],
        engine_override: EngineId | None,
    ) -> None:
        scheduler.submit(
            ThreadJob(
                priority=0,
                name="telegram.run",
                fn=partial(
                    run_engine,
                    cfg,
                    chat_id,
                    user_msg_id,
                    prompt_text,
                    resume_token,
                    context,
                    thread_id,
                    chat_session_key,
                    reply_ref,
                    on_thread_known,
                    engine_override,
                ),
            )
        )

    async def handle_thread_job(job: ThreadJob) -> None:
        try:
            await job.fn()
        except Exception:
            logger.exception("telegram.task.failed", job=job.name)

    async def handle_actions(job: ThreadJob) -> None:
        await job.fn()

    async def handle_incoming(
        msg: TelegramIncomingMessage,
        task_group: TaskGroup,
        inline_router: Callable[[TelegramIncomingMessage], object],
    ) -> None:
        if not msg.chat_id:
            return
        if cfg.allowed_user_ids and msg.sender_id not in cfg.allowed_user_ids:
            logger.info(
                "telegram.incoming.ignored",
                chat_id=msg.chat_id,
                sender_id=msg.sender_id,
            )
            return
        if msg.sender_id is None:
            logger.info(
                "telegram.incoming.ignored",
                chat_id=msg.chat_id,
                reason="missing_sender",
            )
            return
        if msg.sender_id in cfg.allowed_user_ids:
            logger.debug(
                "telegram.incoming.admin",
                chat_id=msg.chat_id,
                sender_id=msg.sender_id,
                message_id=msg.message_id,
            )
        if cfg.allow_groups is False and msg.chat_type != "private":
            logger.debug(
                "telegram.incoming.ignored",
                chat_id=msg.chat_id,
                sender_id=msg.sender_id,
                reason="allow_groups=false",
            )
            return
        if msg.thread_id is None:
            prompt_mode = "default"
        else:
            prompt_mode = cfg.topics.prompt_mode
        if not msg.text and msg.document is None and msg.voice is None:
            return
        if msg.raw is None:
            await handle_new_prompt(
                msg,
                msg.text or "",
                msg.reply_to_message_id,
                msg.reply_to_text,
                document=msg.document,
            )
            return
        if cfg.msgspec_enabled:
            await _handle_msgspec_prompt(
                msg,
                msg.text or "",
                None,
                scheduler,
                scheduler.note_thread_known,
                None,
            )
            return
        if msg.document is not None:
            await handle_new_prompt(
                msg,
                msg.caption or "",
                msg.reply_to_message_id,
                msg.reply_to_text,
                document=msg.document,
            )
            return
        if msg.thread_id is None:
            await _handle_prompt(
                msg,
                msg.text or "",
                None,
                scheduler,
                scheduler.note_thread_known,
            )
            return
        await handle_new_prompt(
            msg,
            msg.text or "",
            msg.reply_to_message_id,
            msg.reply_to_text,
            document=None,
        )

    async def _update_resolve_topic_context(
        msg: TelegramIncomingMessage,
        resolved: ResolvedMessage,
        topic_key: tuple[int, int] | None,
    ) -> None:
        if resolved.context is None:
            return
        if topic_key is None or state.topic_store is None:
            return
        await state.topic_store.set_context(*topic_key, resolved.context)
        if resolved.context_source == "directives":
            await _maybe_rename_topic(
                cfg,
                state.topic_store,
                chat_id=topic_key[0],
                thread_id=topic_key[1],
                context=resolved.context,
            )

    async def topic_router(msg: TelegramIncomingMessage) -> None:
        reply = make_reply(cfg, msg)
        text = msg.text
        is_voice_transcribed = False
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
        msg_context = await build_message_context(msg)
        chat_id = msg_context.chat_id
        reply_id = msg_context.reply_id
        reply_ref = msg_context.reply_ref
        topic_key = msg_context.topic_key
        chat_session_key = msg_context.chat_session_key
        chat_project = msg_context.chat_project
        ambient_context = msg_context.ambient_context
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
        if msg.thread_id is not None and msg.sender_id is not None:
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
            forward_coalescer.schedule(pending)
            return
        resolved = await resolve_prompt_message(msg, text, ambient_context)
        if resolved is None:
            return
        if is_voice_transcribed:
            resolved = ResolvedMessage(
                prompt=f"(voice transcribed) {resolved.prompt}",
                resume_token=resolved.resume_token,
                engine_override=resolved.engine_override,
                context=resolved.context,
                context_source=resolved.context_source,
            )
        prompt_text = resolved.prompt
        if msg.thread_id is not None and msg.sender_id is not None:
            prompt_text = _format_prompt_line(
                msg,
                prompt_text,
                prompt_mode=cfg.topics.prompt_mode,
            )
        if not prompt_text.strip():
            return
        resume_token = resolved.resume_token
        context = resolved.context
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
            user_msg_id=msg.message_id,
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
                msg.message_id,
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
            user_msg_id=msg.message_id,
            thread_id=msg.thread_id,
            resume_token=resume_token,
            context=context,
        )
        await scheduler.enqueue_resume(
            chat_id,
            msg.message_id,
            prompt_text,
            resume_token,
            context,
            msg.thread_id,
            chat_session_key,
            progress_ref,
        )

    def _handle_prompt_command(
        msg: TelegramIncomingMessage,
        text: str,
        reply_text: str | None,
        *,
        resolved: ResolvedMessage,
        thread_id: int | None,
        topic_key: tuple[int, int] | None,
        chat_session_key: tuple[int, int | None] | None,
    ) -> None:
        prompt = resolved.prompt
        if thread_id is not None and msg.sender_id is not None:
            prompt = _format_prompt_line(
                msg,
                prompt,
                prompt_mode=cfg.topics.prompt_mode,
            )
        if not prompt.strip():
            return
        resume_token = resolved.resume_token
        context = resolved.context
        engine_resolution = await resolve_engine_defaults(
            explicit_engine=resolved.engine_override,
            context=context,
            chat_id=msg.chat_id,
            topic_key=topic_key,
        )
        engine_override = engine_resolution.engine
        if resume_token is None:
            scheduler.submit(
                ThreadJob(
                    priority=0,
                    name="telegram.run",
                    fn=partial(
                        run_engine,
                        cfg,
                        msg.chat_id,
                        msg.message_id,
                        prompt,
                        resume_token,
                        context,
                        thread_id,
                        chat_session_key,
                        MessageRef(
                            channel_id=msg.chat_id,
                            message_id=msg.message_id,
                            thread_id=thread_id,
                        ),
                        scheduler.note_thread_known,
                        engine_override,
                    ),
                )
            )
            return
        scheduler.submit(
            ThreadJob(
                priority=0,
                name="telegram.resume",
                fn=partial(
                    scheduler.enqueue_resume,
                    msg.chat_id,
                    msg.message_id,
                    prompt,
                    resume_token,
                    context,
                    thread_id,
                    chat_session_key,
                    MessageRef(
                        channel_id=msg.chat_id,
                        message_id=msg.message_id,
                        thread_id=thread_id,
                    ),
                ),
            )
        )

    async def _refresh_files_state(cfg: TelegramBridgeConfig) -> None:
        try:
            state.files_enabled = cfg.files.enabled
        except Exception:
            logger.exception("telegram.files.state.failed")

    async def _refresh_topics_state(cfg: TelegramBridgeConfig) -> None:
        try:
            state.topics_enabled = cfg.topics.enabled
        except Exception:
            logger.exception("telegram.topics.state.failed")

    async def _refresh_topics_scope(cfg: TelegramBridgeConfig) -> None:
        try:
            refresh_topics_scope()
        except Exception:
            logger.exception("telegram.topics.scope.failed")

    async def _setup_topics(cfg: TelegramBridgeConfig) -> None:
        if cfg.topics.enabled:
            if cfg.config_path is None:
                raise ConfigError(
                    "topics enabled but config path is not set; cannot locate state file."
                )
            state.topic_store = TopicStateStore(resolve_state_path(cfg.config_path))
            await _validate_topics_setup(cfg)
            refresh_topics_scope()
            logger.info(
                "topics.enabled",
                scope=cfg.topics.scope,
                resolved_scope=state.resolved_topics_scope,
            )
        else:
            state.topic_store = None

    async def _setup_chat_sessions(cfg: TelegramBridgeConfig) -> None:
        if cfg.session_mode == "chat":
            if cfg.config_path is None:
                raise ConfigError(
                    "chat sessions enabled but config path is not set; cannot locate state file."
                )
            state.chat_session_store = ChatSessionStore(
                resolve_sessions_path(cfg.config_path)
            )
            logger.info("chat.sessions.enabled")
        else:
            state.chat_session_store = None

    async def _setup_chat_prefs(cfg: TelegramBridgeConfig) -> None:
        if cfg.config_path is None:
            return
        state.chat_prefs = ChatPrefsStore(resolve_prefs_path(cfg.config_path))

    def resolve_chat_session_key(
        msg: TelegramIncomingMessage,
    ) -> tuple[int, int | None] | None:
        if state.chat_session_store is None or msg.thread_id is not None:
            return None
        if msg.chat_type == "private":
            return (msg.chat_id, None)
        if msg.sender_id is None:
            return None
        return (msg.chat_id, msg.sender_id)

    async def handle_prompt_command(
        msg: TelegramIncomingMessage,
        text: str,
        reply_text: str | None,
        *,
        resolved: ResolvedMessage,
        thread_id: int | None,
        topic_key: tuple[int, int] | None,
        chat_session_key: tuple[int, int | None] | None,
    ) -> None:
        if not text.strip():
            return
        _handle_prompt_command(
            msg,
            text,
            reply_text,
            resolved=resolved,
            thread_id=thread_id,
            topic_key=topic_key,
            chat_session_key=chat_session_key,
        )

    async def _handle_file_put_raw(
        cfg: TelegramBridgeConfig,
        msg: TelegramIncomingMessage,
        document: dict[str, object],
        ambient_context: RunContext | None,
    ) -> None:
        if msg.document is None:
            return
        file_name = msg.document.file_name
        file_id = msg.document.file_id
        saved = await cfg.exec_cfg.transport.get_file(
            file_id,
            file_name,
            from_chat=msg.chat_id,
        )
        if saved is None:
            return
        prompt = f"[uploaded file: {saved.rel_path.as_posix()}]"
        prompt_text = _format_prompt_line(
            msg,
            prompt,
            prompt_mode=cfg.topics.prompt_mode,
        )
        if not prompt_text.strip():
            return
        await run_job(
            msg.chat_id,
            msg.message_id,
            prompt_text,
            None,
            ambient_context,
            msg.thread_id,
            None,
            None,
            scheduler.note_thread_known,
            None,
        )

    async def handle_file_put(msg: TelegramIncomingMessage) -> None:
        if msg.document is None:
            return
        if msg.thread_id is None and not cfg.files.auto_put:
            await handle_file_put_default(
                cfg,
                msg,
                None,
                state.topic_store,
            )
            return
        prompt = ""
        if msg.caption:
            prompt = msg.caption
        prompt_text = _format_prompt_line(
            msg,
            prompt,
            prompt_mode=cfg.topics.prompt_mode,
        )
        if not prompt_text.strip():
            return
        await run_job(
            msg.chat_id,
            msg.message_id,
            prompt_text,
            None,
            None,
            msg.thread_id,
            None,
            None,
            scheduler.note_thread_known,
            None,
        )

    async def _handle_file_command(
        msg: TelegramIncomingMessage,
        text: str,
        reply_text: str | None,
    ) -> None:
        reply = make_reply(cfg, msg)
        if msg.thread_id is not None:
            if state.topic_store is not None and msg.sender_id is not None:
                pending = _PendingPrompt(
                    msg=msg,
                    text=text,
                    ambient_context=None,
                    chat_project=None,
                    topic_key=None,
                    chat_session_key=None,
                    reply_ref=None,
                    reply_id=None,
                    is_voice_transcribed=False,
                    forwards=[],
                )
                forward_coalescer.schedule(pending)
                return
        resolved = cfg.runtime.resolve_message(
            text=text,
            reply_text=reply_text,
            ambient_context=None,
            chat_id=msg.chat_id,
        )
        _handle_prompt_command(
            msg,
            text,
            reply_text,
            resolved=ResolvedMessage(
                prompt=resolved.prompt,
                resume_token=resolved.resume_token,
                engine_override=resolved.engine_override,
                context=resolved.context,
                context_source=resolved.context_source,
            ),
            thread_id=msg.thread_id,
            topic_key=None,
            chat_session_key=None,
        )

    async def handle_command(msg: TelegramIncomingMessage) -> None:
        text = msg.text
        command_id, args_text = parse_slash_command(text)
        if command_id is None:
            return
        reply_text = msg.reply_to_text
        if command_id not in state.command_ids:
            refresh_commands()
        if command_id not in state.command_ids:
            return
        await handle_prompt_command(
            msg,
            text,
            reply_text,
            resolved=cfg.runtime.resolve_message(
                text=text,
                reply_text=reply_text,
                ambient_context=None,
                chat_id=msg.chat_id,
            ),
            thread_id=msg.thread_id,
            topic_key=resolve_topic_key(msg),
            chat_session_key=_chat_session_key(msg, store=state.chat_session_store),
        )

    async def handle_callback(update: TelegramCallbackQuery) -> None:
        if update.data == CANCEL_CALLBACK_DATA:
            await handle_callback_cancel(cfg, update, state.running_tasks, scheduler)
            return
        await cfg.bot.answer_callback_query(update.callback_query_id)

    async def _handle_direct_message(msg: TelegramIncomingMessage) -> None:
        if cfg.allow_direct is False:
            return
        await handle_new_prompt(
            msg,
            msg.text or "",
            msg.reply_to_message_id,
            msg.reply_to_text,
            document=msg.document,
        )

    async def _handle_group_message(msg: TelegramIncomingMessage) -> None:
        if msg.thread_id is None:
            await _handle_prompt(
                msg,
                msg.text or "",
                None,
                scheduler,
                scheduler.note_thread_known,
            )
            return
        await topic_router(msg)

    async def _handle_topic_message(msg: TelegramIncomingMessage) -> None:
        await topic_router(msg)

    async def _handle_group_document(msg: TelegramIncomingMessage) -> None:
        if msg.thread_id is None and cfg.files.auto_put:
            await handle_file_put(msg)
            return
        if msg.thread_id is not None and cfg.files.enabled and cfg.files.auto_put:
            await handle_file_put(msg)
            return
        await handle_new_prompt(
            msg,
            msg.caption or "",
            msg.reply_to_message_id,
            msg.reply_to_text,
            document=msg.document,
        )

    async def _handle_group_voice(msg: TelegramIncomingMessage) -> None:
        await _handle_prompt(
            msg,
            msg.text or "",
            None,
            scheduler,
            scheduler.note_thread_known,
        )

    async def _handle_message(msg: TelegramIncomingMessage) -> None:
        if msg.raw is None:
            await handle_new_prompt(
                msg,
                msg.text or "",
                msg.reply_to_message_id,
                msg.reply_to_text,
                document=msg.document,
            )
            return
        if msg.chat_type == "private":
            await _handle_direct_message(msg)
            return
        if msg.document is not None:
            await _handle_group_document(msg)
            return
        if msg.voice is not None:
            await _handle_group_voice(msg)
            return
        await _handle_group_message(msg)

    async def _handle_raw_update(update: TelegramIncomingUpdate) -> None:
        msg = update
        if isinstance(update, TelegramCallbackQuery):
            await handle_callback(update)
            return
        if update.sender_id in cfg.allowed_user_ids:
            logger.debug(
                "telegram.update.admin",
                chat_id=update.chat_id,
                sender_id=update.sender_id,
                message_id=update.message_id,
            )
        if update.sender_id is None:
            logger.info(
                "telegram.update.ignored",
                chat_id=update.chat_id,
                reason="missing_sender",
            )
            return
        if cfg.allow_groups is False and update.chat_type != "private":
            logger.info(
                "telegram.update.ignored",
                chat_id=update.chat_id,
                reason="allow_groups=false",
            )
            return
        if update.chat_type == "private":
            await _handle_direct_message(update)
            return
        if update.is_topic_message:
            await _handle_topic_message(update)
            return
        if update.document is not None:
            await _handle_group_document(update)
            return
        if update.voice is not None:
            await _handle_group_voice(update)
            return
        await _handle_group_message(update)

    async def _handle_events() -> None:
        async for msg in poll_updates(cfg):
            await handle_incoming(msg, tg, _dispatch_message)

    async def _reload_config(cfg: TelegramBridgeConfig, cfg_path: Path) -> None:
        logger.info("telegram.config.reload.starting")
        try:
            await refresh_config()
        except Exception:
            logger.exception("telegram.config.reload.failed")
            return
        if cfg_path != cfg.config_path:
            logger.info(
                "telegram.config.reload", cfg_path=str(cfg_path), action="updated"
            )
        else:
            logger.info("telegram.config.reload", action="unchanged")

    async def _send_config_updates(cfg: TelegramBridgeConfig) -> None:
        await send_plain(cfg, chat_id=cfg.chat_id, text="config updated.")

    async def _reload_config_if_updated() -> None:
        if cfg.config_path is None:
            return
        async for update in watch_config_changes(cfg.config_path):
            if update.event not in {ConfigReload.CHANGED, ConfigReload.CREATED}:
                continue
            await _reload_config(cfg, update.path)
            await _send_config_updates(cfg)

    async def _run_loop() -> None:
        async with anyio.create_task_group() as tg:
            refresh_commands()
            refresh_reserved_commands()
            refresh_topics_scope()
            update_command_menu()
            update_transport_snapshot()
            await _setup_topics(cfg)
            await _setup_chat_sessions(cfg)
            await _setup_chat_prefs(cfg)
            topic_router = _topic_router(route_message)
            if cfg.msgspec_enabled:
                dispatch = _log_router(_dispatch_message)
            async for update in poll_updates(cfg):
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
                    continue
                await route_message(update)

    if cfg.config_path is None:
        raise ConfigError("config path is required")
    await _setup_topics(cfg)
    await _setup_chat_sessions(cfg)
    await _setup_chat_prefs(cfg)

    if cfg.session_mode == "chat":
        handle_incoming_fn = _handle_raw_update
    else:
        handle_incoming_fn = handle_incoming

    if cfg.log_json:
        handle_incoming_fn = _log_router(handle_incoming_fn)

    if cfg.command_menu:
        update_command_menu()

    refresh_commands()
    refresh_reserved_commands()
    refresh_topics_scope()
    update_transport_snapshot()

    if cfg.config_path is None:
        raise ConfigError("config path is required")

    if cfg.thread_pool:
        async with anyio.create_task_group() as tg:
            await tg.start(scheduler.start)
            tg.start_soon(_reload_config_if_updated)
            tg.start_soon(_handle_events)
    else:
        async with anyio.create_task_group() as tg:
            tg.start_soon(_reload_config_if_updated)
            tg.start_soon(_handle_events)
