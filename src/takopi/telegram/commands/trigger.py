from __future__ import annotations

from typing import TYPE_CHECKING

from ..chat_prefs import ChatPrefsStore
from ..files import split_command_args
from ..topic_state import TopicStateStore
from ..topics import _topic_key
from ..trigger_mode import resolve_trigger_mode
from ..types import TelegramIncomingMessage
from .reply import make_reply

if TYPE_CHECKING:
    from ..bridge import TelegramBridgeConfig

TRIGGER_USAGE = (
    "usage: `/trigger`, `/trigger all`, `/trigger mentions`, or `/trigger clear`"
)


async def _check_trigger_permissions(
    cfg: TelegramBridgeConfig, msg: TelegramIncomingMessage
) -> bool:
    reply = make_reply(cfg, msg)
    sender_id = msg.sender_id
    if sender_id is None:
        await reply(text="cannot verify sender for trigger settings.")
        return False
    is_private = msg.chat_type == "private"
    if msg.chat_type is None:
        is_private = msg.chat_id > 0
    if is_private:
        return True
    member = await cfg.bot.get_chat_member(msg.chat_id, sender_id)
    if member is None:
        await reply(text="failed to verify trigger permissions.")
        return False
    if member.status in {"creator", "administrator"}:
        return True
    await reply(text="changing trigger mode is restricted to group admins.")
    return False


async def _handle_trigger_command(
    cfg: TelegramBridgeConfig,
    msg: TelegramIncomingMessage,
    args_text: str,
    _ambient_context,
    topic_store: TopicStateStore | None,
    chat_prefs: ChatPrefsStore | None,
    *,
    resolved_scope: str | None = None,
    scope_chat_ids: frozenset[int] | None = None,
) -> None:
    reply = make_reply(cfg, msg)
    tkey = (
        _topic_key(msg, cfg, scope_chat_ids=scope_chat_ids)
        if topic_store is not None
        else None
    )
    tokens = split_command_args(args_text)
    action = tokens[0].lower() if tokens else "show"

    if action in {"show", ""}:
        resolved = await resolve_trigger_mode(
            chat_id=msg.chat_id,
            thread_id=msg.thread_id,
            chat_prefs=chat_prefs,
            topic_store=topic_store,
        )
        topic_mode = None
        if tkey is not None and topic_store is not None:
            topic_mode = await topic_store.get_trigger_mode(tkey[0], tkey[1])
        chat_mode = None
        if chat_prefs is not None:
            chat_mode = await chat_prefs.get_trigger_mode(msg.chat_id)
        if topic_mode is not None:
            source = "topic override"
        elif chat_mode is not None:
            source = "chat default"
        else:
            source = "default"
        trigger_line = f"trigger: {resolved} ({source})"
        topic_label = topic_mode or "none"
        if tkey is None:
            topic_label = "none"
        chat_label = "unavailable" if chat_prefs is None else chat_mode or "none"
        defaults_line = f"defaults: topic: {topic_label}, chat: {chat_label}"
        available_line = "available: all, mentions"
        await reply(text="\n\n".join([trigger_line, defaults_line, available_line]))
        return

    if action in {"all", "mentions"}:
        if not await _check_trigger_permissions(cfg, msg):
            return
        if tkey is not None:
            if topic_store is None:
                await reply(text="topic trigger settings are unavailable.")
                return
            await topic_store.set_trigger_mode(tkey[0], tkey[1], action)
            await reply(text=f"topic trigger mode set to `{action}`")
            return
        if chat_prefs is None:
            await reply(text="chat trigger settings are unavailable (no config path).")
            return
        await chat_prefs.set_trigger_mode(msg.chat_id, action)
        await reply(text=f"chat trigger mode set to `{action}`")
        return

    if action == "clear":
        if not await _check_trigger_permissions(cfg, msg):
            return
        if tkey is not None:
            if topic_store is None:
                await reply(text="topic trigger settings are unavailable.")
                return
            await topic_store.clear_trigger_mode(tkey[0], tkey[1])
            await reply(text="topic trigger mode cleared (using chat default).")
            return
        if chat_prefs is None:
            await reply(text="chat trigger settings are unavailable (no config path).")
            return
        await chat_prefs.clear_trigger_mode(msg.chat_id)
        await reply(text="chat trigger mode reset to `all`.")
        return

    await reply(text=TRIGGER_USAGE)
