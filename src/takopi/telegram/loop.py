from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from functools import partial
from typing import cast

import anyio
from anyio.abc import TaskGroup

from ..config import ConfigError
from ..config_watch import ConfigReload, watch_config as watch_config_changes
from ..commands import list_command_ids
from ..directives import DirectiveError
from ..logging import get_logger
from ..model import EngineId, ResumeToken
from ..scheduler import ThreadJob, ThreadScheduler
from ..progress import ProgressTracker
from ..settings import TelegramTransportSettings
from ..transport import MessageRef, SendOptions
from ..transport_runtime import ResolvedMessage
from ..context import RunContext
from .bridge import CANCEL_CALLBACK_DATA, TelegramBridgeConfig, send_plain
from .commands.agent import _handle_agent_command
from .commands.cancel import handle_callback_cancel, handle_cancel
from .commands.dispatch import _dispatch_command
from .commands.executor import _run_engine, _should_show_resume_line
from .commands.file_transfer import (
    FILE_PUT_USAGE,
    _handle_file_command,
    _handle_file_put_default,
    _save_file_put,
)
from .commands.media import _handle_media_group
from .commands.menu import _reserved_commands, _set_command_menu
from .commands.parse import _parse_slash_command, is_cancel_command
from .commands.reply import make_reply
from .commands.topics import (
    _handle_chat_new_command,
    _handle_ctx_command,
    _handle_new_command,
    _handle_topic_command,
)
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
from .engine_defaults import resolve_engine_for_message
from .topic_state import TopicStateStore, resolve_state_path
from .types import (
    TelegramCallbackQuery,
    TelegramIncomingMessage,
    TelegramIncomingUpdate,
)
from .voice import transcribe_voice

logger = get_logger(__name__)

__all__ = ["poll_updates", "run_main_loop", "send_with_resume"]

_MEDIA_GROUP_DEBOUNCE_S = 1.0


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


def _allowed_chat_ids(cfg: TelegramBridgeConfig) -> set[int]:
    allowed = set(cfg.chat_ids or ())
    allowed.add(cfg.chat_id)
    allowed.update(cfg.runtime.project_chat_ids())
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
    cfg: TelegramBridgeConfig,
    msg: TelegramIncomingMessage,
    command_id: str,
    args_text: str,
    ambient_context: RunContext | None,
    topic_store: TopicStateStore | None,
    chat_prefs: ChatPrefsStore | None,
    resolved_scope: str | None,
    scope_chat_ids: frozenset[int],
    reply: Callable[..., Awaitable[None]],
    task_group: TaskGroup,
) -> bool:
    handlers: dict[str, Callable[[], Awaitable[None]]] = {}

    if command_id == "file":
        if not cfg.files.enabled:
            handlers["file"] = partial(
                reply,
                text="file transfer disabled; enable `[transports.telegram.files]`.",
            )
        else:
            handlers["file"] = partial(
                _handle_file_command,
                cfg,
                msg,
                args_text,
                ambient_context,
                topic_store,
            )

    if cfg.topics.enabled and topic_store is not None:
        handlers.update(
            {
                "ctx": partial(
                    _handle_ctx_command,
                    cfg,
                    msg,
                    args_text,
                    topic_store,
                    resolved_scope=resolved_scope,
                    scope_chat_ids=scope_chat_ids,
                ),
                "new": partial(
                    _handle_new_command,
                    cfg,
                    msg,
                    topic_store,
                    resolved_scope=resolved_scope,
                    scope_chat_ids=scope_chat_ids,
                ),
                "topic": partial(
                    _handle_topic_command,
                    cfg,
                    msg,
                    args_text,
                    topic_store,
                    resolved_scope=resolved_scope,
                    scope_chat_ids=scope_chat_ids,
                ),
            }
        )

    if command_id == "agent":
        handlers["agent"] = partial(
            _handle_agent_command,
            cfg,
            msg,
            args_text,
            ambient_context,
            topic_store,
            chat_prefs,
            resolved_scope=resolved_scope,
            scope_chat_ids=scope_chat_ids,
        )

    handler = handlers.get(command_id)
    if handler is None:
        return False
    task_group.start_soon(handler)
    return True


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
) -> AsyncIterator[TelegramIncomingUpdate]:
    offset: int | None = None
    offset = await _drain_backlog(cfg, offset)
    await _send_startup(cfg)

    async for msg in poll_incoming(
        cfg.bot,
        chat_ids=lambda: _allowed_chat_ids(cfg),
        offset=offset,
    ):
        yield msg


@dataclass(slots=True)
class _MediaGroupState:
    messages: list[TelegramIncomingMessage]
    token: int = 0


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
) -> None:
    from ..runner_bridge import RunningTasks

    running_tasks: RunningTasks = {}
    command_ids = {
        command_id.lower()
        for command_id in list_command_ids(allowlist=cfg.runtime.allowlist)
    }
    reserved_commands = _reserved_commands(cfg.runtime)
    transport_snapshot = (
        transport_config.model_dump() if transport_config is not None else None
    )
    topic_store: TopicStateStore | None = None
    chat_session_store: ChatSessionStore | None = None
    chat_prefs: ChatPrefsStore | None = None
    media_groups: dict[tuple[int, str], _MediaGroupState] = {}
    resolved_topics_scope: str | None = None
    topics_chat_ids: frozenset[int] = frozenset()

    def refresh_topics_scope() -> None:
        nonlocal resolved_topics_scope, topics_chat_ids
        if cfg.topics.enabled:
            resolved_topics_scope, topics_chat_ids = _resolve_topics_scope(cfg)
        else:
            resolved_topics_scope = None
            topics_chat_ids = frozenset()

    def refresh_commands() -> None:
        nonlocal command_ids, reserved_commands
        allowlist = cfg.runtime.allowlist
        command_ids = {
            command_id.lower() for command_id in list_command_ids(allowlist=allowlist)
        }
        reserved_commands = _reserved_commands(cfg.runtime)

    try:
        config_path = cfg.runtime.config_path
        if config_path is not None:
            chat_prefs = ChatPrefsStore(resolve_prefs_path(config_path))
            logger.info(
                "chat_prefs.enabled",
                state_path=str(resolve_prefs_path(config_path)),
            )
        if cfg.session_mode == "chat":
            if config_path is None:
                raise ConfigError(
                    "session_mode=chat but config path is not set; cannot locate state file."
                )
            chat_session_store = ChatSessionStore(resolve_sessions_path(config_path))
            logger.info(
                "chat_sessions.enabled",
                state_path=str(resolve_sessions_path(config_path)),
            )
        if cfg.topics.enabled:
            if config_path is None:
                raise ConfigError(
                    "topics enabled but config path is not set; cannot locate state file."
                )
            topic_store = TopicStateStore(resolve_state_path(config_path))
            await _validate_topics_setup(cfg)
            refresh_topics_scope()
            logger.info(
                "topics.enabled",
                scope=cfg.topics.scope,
                resolved_scope=resolved_topics_scope,
                state_path=str(resolve_state_path(config_path)),
            )
        await _set_command_menu(cfg)
        async with anyio.create_task_group() as tg:
            config_path = cfg.runtime.config_path
            watch_enabled = bool(watch_config) and config_path is not None

            async def handle_reload(reload: ConfigReload) -> None:
                nonlocal transport_snapshot, transport_id
                refresh_commands()
                refresh_topics_scope()
                await _set_command_menu(cfg)
                if transport_snapshot is not None:
                    new_snapshot = reload.settings.transports.telegram.model_dump()
                    changed = _diff_keys(transport_snapshot, new_snapshot)
                    if changed:
                        logger.warning(
                            "config.reload.transport_config_changed",
                            transport="telegram",
                            keys=changed,
                            restart_required=True,
                        )
                        transport_snapshot = new_snapshot
                if (
                    transport_id is not None
                    and reload.settings.transport != transport_id
                ):
                    logger.warning(
                        "config.reload.transport_changed",
                        old=transport_id,
                        new=reload.settings.transport,
                        restart_required=True,
                    )
                    transport_id = reload.settings.transport

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
                    if topic_store is not None and topic_key is not None:
                        await topic_store.set_session_resume(
                            topic_key[0], topic_key[1], token
                        )
                    if chat_session_store is not None and chat_session_key is not None:
                        await chat_session_store.set_session_resume(
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
                    if topic_store is not None
                    and thread_id is not None
                    and _topics_chat_allowed(
                        cfg, chat_id, scope_chat_ids=topics_chat_ids
                    )
                    else None
                )
                stateful_mode = topic_key is not None or chat_session_key is not None
                show_resume_line = _should_show_resume_line(
                    show_resume_line=cfg.show_resume_line,
                    stateful_mode=stateful_mode,
                    context=context,
                )
                await _run_engine(
                    exec_cfg=cfg.exec_cfg,
                    runtime=cfg.runtime,
                    running_tasks=running_tasks,
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
                topic_key = (
                    _topic_key(msg, cfg, scope_chat_ids=topics_chat_ids)
                    if topic_store is not None
                    else None
                )
                effective_context = ambient_context
                if (
                    topic_store is not None
                    and topic_key is not None
                    and resolved.context is not None
                    and resolved.context_source == "directives"
                ):
                    await topic_store.set_context(*topic_key, resolved.context)
                    await _maybe_rename_topic(
                        cfg,
                        topic_store,
                        chat_id=topic_key[0],
                        thread_id=topic_key[1],
                        context=resolved.context,
                    )
                    effective_context = resolved.context
                if (
                    topic_store is not None
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
                    topic_store=topic_store,
                    chat_prefs=chat_prefs,
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
                chat_session_key = _chat_session_key(msg, store=chat_session_store)
                topic_key = (
                    _topic_key(msg, cfg, scope_chat_ids=topics_chat_ids)
                    if topic_store is not None
                    else None
                )
                engine_resolution = await resolve_engine_defaults(
                    explicit_engine=resolved.engine_override,
                    context=context,
                    chat_id=chat_id,
                    topic_key=topic_key,
                )
                engine_override = engine_resolution.engine
                if resume_token is None and reply_id is not None:
                    running_task = running_tasks.get(
                        MessageRef(channel_id=chat_id, message_id=reply_id)
                    )
                    if running_task is not None:
                        tg.start_soon(
                            send_with_resume,
                            cfg,
                            scheduler.enqueue_resume,
                            running_task,
                            chat_id,
                            user_msg_id,
                            msg.thread_id,
                            chat_session_key,
                            prompt_text,
                        )
                        return
                if (
                    resume_token is None
                    and topic_store is not None
                    and topic_key is not None
                ):
                    engine_for_session = engine_resolution.engine
                    stored = await topic_store.get_session_resume(
                        topic_key[0], topic_key[1], engine_for_session
                    )
                    if stored is not None:
                        resume_token = stored
                if (
                    resume_token is None
                    and chat_session_store is not None
                    and chat_session_key is not None
                ):
                    engine_for_session = engine_resolution.engine
                    stored = await chat_session_store.get_session_resume(
                        chat_session_key[0],
                        chat_session_key[1],
                        engine_for_session,
                    )
                    if stored is not None:
                        resume_token = stored
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
                saved = await _save_file_put(
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

            async def flush_media_group(key: tuple[int, str]) -> None:
                while True:
                    state = media_groups.get(key)
                    if state is None:
                        return
                    token = state.token
                    await anyio.sleep(_MEDIA_GROUP_DEBOUNCE_S)
                    state = media_groups.get(key)
                    if state is None:
                        return
                    if state.token != token:
                        continue
                    messages = list(state.messages)
                    del media_groups[key]
                    await _handle_media_group(
                        cfg,
                        messages,
                        topic_store,
                        run_prompt_from_upload,
                        resolve_prompt_message,
                    )
                    return

            async for msg in poller(cfg):
                if isinstance(msg, TelegramCallbackQuery):
                    if msg.data == CANCEL_CALLBACK_DATA:
                        tg.start_soon(
                            handle_callback_cancel, cfg, msg, running_tasks, scheduler
                        )
                    else:
                        tg.start_soon(
                            cfg.bot.answer_callback_query,
                            msg.callback_query_id,
                        )
                    continue
                user_msg_id = msg.message_id
                chat_id = msg.chat_id
                reply_id = msg.reply_to_message_id
                reply_ref = (
                    MessageRef(channel_id=chat_id, message_id=reply_id)
                    if reply_id is not None
                    else None
                )
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
                    )
                    if text is None:
                        continue
                    is_voice_transcribed = True
                topic_key = (
                    _topic_key(msg, cfg, scope_chat_ids=topics_chat_ids)
                    if topic_store is not None
                    else None
                )
                chat_session_key = _chat_session_key(msg, store=chat_session_store)
                stateful_mode = topic_key is not None or chat_session_key is not None
                chat_project = (
                    _topics_chat_project(cfg, chat_id) if cfg.topics.enabled else None
                )
                bound_context = (
                    await topic_store.get_context(*topic_key)
                    if topic_store is not None and topic_key is not None
                    else None
                )
                ambient_context = _merge_topic_context(
                    chat_project=chat_project, bound=bound_context
                )

                if (
                    cfg.files.enabled
                    and msg.document is not None
                    and msg.media_group_id is not None
                ):
                    key = (chat_id, msg.media_group_id)
                    state = media_groups.get(key)
                    if state is None:
                        state = _MediaGroupState(messages=[])
                        media_groups[key] = state
                        tg.start_soon(flush_media_group, key)
                    state.messages.append(msg)
                    state.token += 1
                    continue

                if is_cancel_command(text):
                    tg.start_soon(handle_cancel, cfg, msg, running_tasks, scheduler)
                    continue

                command_id, args_text = _parse_slash_command(text)
                if command_id == "new":
                    if topic_store is not None and topic_key is not None:
                        tg.start_soon(
                            partial(
                                _handle_new_command,
                                cfg,
                                msg,
                                topic_store,
                                resolved_scope=resolved_topics_scope,
                                scope_chat_ids=topics_chat_ids,
                            )
                        )
                        continue
                    if chat_session_store is not None:
                        tg.start_soon(
                            _handle_chat_new_command,
                            cfg,
                            msg,
                            chat_session_store,
                            chat_session_key,
                        )
                        continue
                    if topic_store is not None:
                        tg.start_soon(
                            partial(
                                _handle_new_command,
                                cfg,
                                msg,
                                topic_store,
                                resolved_scope=resolved_topics_scope,
                                scope_chat_ids=topics_chat_ids,
                            )
                        )
                        continue
                if command_id is not None and _dispatch_builtin_command(
                    cfg=cfg,
                    msg=msg,
                    command_id=command_id,
                    args_text=args_text,
                    ambient_context=ambient_context,
                    topic_store=topic_store,
                    chat_prefs=chat_prefs,
                    resolved_scope=resolved_topics_scope,
                    scope_chat_ids=topics_chat_ids,
                    reply=reply,
                    task_group=tg,
                ):
                    continue
                if msg.document is not None:
                    if cfg.files.enabled and cfg.files.auto_put:
                        caption_text = text.strip()
                        if cfg.files.auto_put_mode == "prompt" and caption_text:
                            tg.start_soon(
                                handle_prompt_upload,
                                msg,
                                caption_text,
                                ambient_context,
                                topic_store,
                            )
                        elif not caption_text:
                            tg.start_soon(
                                _handle_file_put_default,
                                cfg,
                                msg,
                                ambient_context,
                                topic_store,
                            )
                        else:
                            tg.start_soon(
                                partial(reply, text=FILE_PUT_USAGE),
                            )
                    elif cfg.files.enabled:
                        tg.start_soon(
                            partial(reply, text=FILE_PUT_USAGE),
                        )
                    continue
                if command_id is not None and command_id not in reserved_commands:
                    if command_id not in command_ids:
                        refresh_commands()
                    if command_id in command_ids:
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
                        tg.start_soon(
                            _dispatch_command,
                            cfg,
                            msg,
                            text,
                            command_id,
                            args_text,
                            running_tasks,
                            scheduler,
                            wrap_on_thread_known(
                                scheduler.note_thread_known,
                                topic_key,
                                chat_session_key,
                            ),
                            stateful_mode,
                            default_engine_override,
                        )
                        continue

                reply_text = msg.reply_to_text
                try:
                    resolved = cfg.runtime.resolve_message(
                        text=text,
                        reply_text=reply_text,
                        ambient_context=ambient_context,
                        chat_id=chat_id,
                    )
                except DirectiveError as exc:
                    await reply(text=f"error:\n{exc}")
                    continue
                if is_voice_transcribed:
                    resolved = ResolvedMessage(
                        prompt=f"(voice transcribed) {resolved.prompt}",
                        resume_token=resolved.resume_token,
                        engine_override=resolved.engine_override,
                        context=resolved.context,
                        context_source=resolved.context_source,
                    )

                text = resolved.prompt
                resume_token = resolved.resume_token
                context = resolved.context
                engine_resolution = await resolve_engine_defaults(
                    explicit_engine=resolved.engine_override,
                    context=context,
                    chat_id=chat_id,
                    topic_key=topic_key,
                )
                engine_override = engine_resolution.engine
                if (
                    topic_store is not None
                    and topic_key is not None
                    and resolved.context is not None
                    and resolved.context_source == "directives"
                ):
                    await topic_store.set_context(*topic_key, resolved.context)
                    await _maybe_rename_topic(
                        cfg,
                        topic_store,
                        chat_id=topic_key[0],
                        thread_id=topic_key[1],
                        context=resolved.context,
                    )
                    ambient_context = resolved.context
                if (
                    topic_store is not None
                    and topic_key is not None
                    and ambient_context is None
                    and resolved.context_source not in {"directives", "reply_ctx"}
                ):
                    await reply(
                        text="this topic isn't bound to a project yet.\n"
                        f"{_usage_ctx_set(chat_project=chat_project)} or "
                        f"{_usage_topic(chat_project=chat_project)}",
                    )
                    continue
                if resume_token is None and reply_id is not None:
                    running_task = running_tasks.get(
                        MessageRef(channel_id=chat_id, message_id=reply_id)
                    )
                    if running_task is not None:
                        tg.start_soon(
                            send_with_resume,
                            cfg,
                            scheduler.enqueue_resume,
                            running_task,
                            chat_id,
                            user_msg_id,
                            msg.thread_id,
                            chat_session_key,
                            text,
                        )
                        continue
                if (
                    resume_token is None
                    and topic_store is not None
                    and topic_key is not None
                ):
                    engine_for_session = engine_resolution.engine
                    stored = await topic_store.get_session_resume(
                        topic_key[0], topic_key[1], engine_for_session
                    )
                    if stored is not None:
                        resume_token = stored
                if (
                    resume_token is None
                    and chat_session_store is not None
                    and chat_session_key is not None
                ):
                    engine_for_session = engine_resolution.engine
                    stored = await chat_session_store.get_session_resume(
                        chat_session_key[0], chat_session_key[1], engine_for_session
                    )
                    if stored is not None:
                        resume_token = stored

                if resume_token is None:
                    tg.start_soon(
                        run_job,
                        chat_id,
                        user_msg_id,
                        text,
                        None,
                        context,
                        msg.thread_id,
                        chat_session_key,
                        reply_ref,
                        scheduler.note_thread_known,
                        engine_override,
                    )
                else:
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
                        text,
                        resume_token,
                        context,
                        msg.thread_id,
                        chat_session_key,
                        progress_ref,
                    )
    finally:
        await cfg.exec_cfg.transport.close()
