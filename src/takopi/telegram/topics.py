from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from ..config import ConfigError
from ..context import RunContext
from ..settings import TelegramTopicsSettings
from ..transport_runtime import TransportRuntime
from .client import BotClient
from .topic_state import TopicStateStore, TopicThreadSnapshot
from .types import TelegramIncomingMessage

if TYPE_CHECKING:
    from .bridge import TelegramBridgeConfig

__all__ = [
    "_TOPICS_COMMANDS",
    "_maybe_rename_topic",
    "_maybe_update_topic_context",
    "_resolve_topics_scope",
    "_topic_key",
    "_topic_title",
    "_topics_chat_allowed",
    "_topics_chat_project",
    "_topics_command_error",
    "_topics_scope_label",
    "_validate_topics_setup",
]

_TOPICS_COMMANDS = {"ctx", "new", "topic"}


def _resolve_topics_scope_raw(
    scope: str, chat_id: int, project_chat_ids: Iterable[int]
) -> tuple[str, frozenset[int]]:
    project_ids = set(project_chat_ids)
    if scope == "auto":
        scope = "projects" if project_ids else "main"
    if scope == "main":
        return scope, frozenset({chat_id})
    if scope == "projects":
        return scope, frozenset(project_ids)
    if scope == "all":
        return scope, frozenset({chat_id, *project_ids})
    raise ValueError(f"Invalid topics.scope: {scope!r}")


def _resolve_topics_scope(cfg: TelegramBridgeConfig) -> tuple[str, frozenset[int]]:
    return _resolve_topics_scope_raw(
        cfg.topics.scope, cfg.chat_id, cfg.runtime.project_chat_ids()
    )


def _topics_scope_label(cfg: TelegramBridgeConfig) -> str:
    resolved, _ = _resolve_topics_scope(cfg)
    if cfg.topics.scope == "auto":
        return f"auto ({resolved})"
    return resolved


def _topics_chat_project(cfg: TelegramBridgeConfig, chat_id: int) -> str | None:
    context = cfg.runtime.default_context_for_chat(chat_id)
    return context.project if context is not None else None


def _topics_chat_allowed(
    cfg: TelegramBridgeConfig,
    chat_id: int,
    *,
    scope_chat_ids: frozenset[int] | None = None,
) -> bool:
    if not cfg.topics.enabled:
        return False
    if scope_chat_ids is None:
        _, scope_chat_ids = _resolve_topics_scope(cfg)
    return chat_id in scope_chat_ids


def _topics_command_error(
    cfg: TelegramBridgeConfig,
    chat_id: int,
    *,
    resolved_scope: str | None = None,
    scope_chat_ids: frozenset[int] | None = None,
) -> str | None:
    if resolved_scope is None or scope_chat_ids is None:
        resolved_scope, scope_chat_ids = _resolve_topics_scope(cfg)
    if cfg.topics.enabled and chat_id in scope_chat_ids:
        return None
    if resolved_scope == "main":
        if cfg.topics.scope == "auto":
            return (
                "topics commands are only available in the main chat (auto scope). "
                'to use topics in project chats, set `topics.scope = "projects"`.'
            )
        return "topics commands are only available in the main chat."
    if resolved_scope == "projects":
        if cfg.topics.scope == "auto":
            return (
                "topics commands are only available in project chats (auto scope). "
                'to use topics in the main chat, set `topics.scope = "main"`.'
            )
        return "topics commands are only available in project chats."
    return "topics commands are only available in the main or project chats."


def _topic_key(
    msg: TelegramIncomingMessage,
    cfg: TelegramBridgeConfig,
    *,
    scope_chat_ids: frozenset[int] | None = None,
) -> tuple[int, int] | None:
    if not cfg.topics.enabled:
        return None
    if not _topics_chat_allowed(cfg, msg.chat_id, scope_chat_ids=scope_chat_ids):
        return None
    if msg.thread_id is None:
        return None
    return (msg.chat_id, msg.thread_id)


def _topic_title(*, runtime: TransportRuntime, context: RunContext) -> str:
    project = (
        runtime.project_alias_for_key(context.project)
        if context.project is not None
        else ""
    )
    if context.branch:
        if project:
            return f"{project} @{context.branch}"
        return f"@{context.branch}"
    return project or "topic"


async def _maybe_rename_topic(
    cfg: TelegramBridgeConfig,
    store: TopicStateStore,
    *,
    chat_id: int,
    thread_id: int,
    context: RunContext,
    snapshot: TopicThreadSnapshot | None = None,
) -> None:
    title = _topic_title(runtime=cfg.runtime, context=context)
    if snapshot is None:
        snapshot = await store.get_thread(chat_id, thread_id)
    if snapshot is not None and snapshot.topic_title == title:
        return
    updated = await cfg.bot.edit_forum_topic(
        chat_id=chat_id,
        message_thread_id=thread_id,
        name=title,
    )
    if not updated:
        from ..logging import get_logger

        logger = get_logger(__name__)
        logger.warning(
            "topics.rename.failed",
            chat_id=chat_id,
            thread_id=thread_id,
            title=title,
        )
        return
    await store.set_context(chat_id, thread_id, context, topic_title=title)


async def _maybe_update_topic_context(
    *,
    cfg: TelegramBridgeConfig,
    topic_store: TopicStateStore | None,
    topic_key: tuple[int, int] | None,
    context: RunContext | None,
    context_source: str,
) -> None:
    if (
        topic_store is None
        or topic_key is None
        or context is None
        or context_source != "directives"
    ):
        return
    await topic_store.set_context(topic_key[0], topic_key[1], context)
    await _maybe_rename_topic(
        cfg,
        topic_store,
        chat_id=topic_key[0],
        thread_id=topic_key[1],
        context=context,
    )


async def _validate_topics_setup(cfg: TelegramBridgeConfig) -> None:
    await _validate_topics_setup_for(
        bot=cfg.bot,
        topics=cfg.topics,
        chat_id=cfg.chat_id,
        project_chat_ids=cfg.runtime.project_chat_ids(),
    )


async def _validate_topics_setup_for(
    *,
    bot: BotClient,
    topics: TelegramTopicsSettings,
    chat_id: int,
    project_chat_ids: Iterable[int],
) -> None:
    if not topics.enabled:
        return
    me = await bot.get_me()
    if me is None:
        raise ConfigError("failed to fetch bot id for topics validation.")
    bot_id = me.id
    scope, chat_ids = _resolve_topics_scope_raw(topics.scope, chat_id, project_chat_ids)
    if scope == "projects" and not chat_ids:
        raise ConfigError(
            "topics enabled but no project chats are configured; "
            'set projects.<alias>.chat_id for forum chats or use scope="main".'
        )

    for chat_id in chat_ids:
        chat = await bot.get_chat(chat_id)
        if chat is None:
            raise ConfigError(
                f"failed to fetch chat info for topics validation ({chat_id})."
            )
        if chat.type != "supergroup":
            raise ConfigError(
                "topics enabled but chat is not a supergroup "
                f"(chat_id={chat_id}); convert the group and enable topics."
            )
        if chat.is_forum is not True:
            raise ConfigError(
                "topics enabled but chat does not have topics enabled "
                f"(chat_id={chat_id}); turn on topics in group settings."
            )
        member = await bot.get_chat_member(chat_id, bot_id)
        if member is None:
            raise ConfigError(
                "failed to fetch bot permissions "
                f"(chat_id={chat_id}); promote the bot to admin with manage topics."
            )
        if member.status == "creator":
            continue
        if member.status != "administrator":
            raise ConfigError(
                "topics enabled but bot is not an admin "
                f"(chat_id={chat_id}); promote it and grant manage topics."
            )
        if member.can_manage_topics is not True:
            raise ConfigError(
                "topics enabled but bot lacks manage topics permission "
                f"(chat_id={chat_id}); grant can_manage_topics."
            )
