from __future__ import annotations

from typing import TYPE_CHECKING

from ..chat_prefs import ChatPrefsStore
from ..files import split_command_args
from ..topic_state import TopicStateStore
from ..topics import _topic_key
from ..trigger_mode import resolve_trigger_mode
from ..types import TelegramIncomingMessage
from .overrides import check_admin_or_private
from .plan import ActionPlan
from .reply import make_reply

if TYPE_CHECKING:
    from ..bridge import TelegramBridgeConfig

TRIGGER_USAGE = (
    "usage: `/trigger`, `/trigger all`, `/trigger mentions`, or `/trigger clear`"
)


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
    plan = await _plan_trigger_command(
        cfg,
        msg,
        args_text=args_text,
        topic_store=topic_store,
        chat_prefs=chat_prefs,
        scope_chat_ids=scope_chat_ids,
    )
    await plan.execute(reply)


async def _plan_trigger_command(
    cfg: TelegramBridgeConfig,
    msg: TelegramIncomingMessage,
    *,
    args_text: str,
    topic_store: TopicStateStore | None,
    chat_prefs: ChatPrefsStore | None,
    scope_chat_ids: frozenset[int] | None,
) -> ActionPlan:
    tkey = _topic_key(msg, cfg, scope_chat_ids=scope_chat_ids)
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
        return ActionPlan(
            reply_text="\n\n".join([trigger_line, defaults_line, available_line])
        )

    if action in {"all", "mentions"}:
        decision = await check_admin_or_private(
            cfg,
            msg,
            missing_sender="cannot verify sender for trigger settings.",
            failed_member="failed to verify trigger permissions.",
            denied="changing trigger mode is restricted to group admins.",
        )
        if not decision.allowed:
            return ActionPlan(reply_text=decision.error_text or TRIGGER_USAGE)
        if tkey is not None:
            if topic_store is None:
                return ActionPlan(reply_text="topic trigger settings are unavailable.")
            return ActionPlan(
                reply_text=f"topic trigger mode set to `{action}`",
                actions=(
                    lambda: topic_store.set_trigger_mode(tkey[0], tkey[1], action),
                ),
            )
        if chat_prefs is None:
            return ActionPlan(
                reply_text="chat trigger settings are unavailable (no config path)."
            )
        return ActionPlan(
            reply_text=f"chat trigger mode set to `{action}`",
            actions=(lambda: chat_prefs.set_trigger_mode(msg.chat_id, action),),
        )

    if action == "clear":
        decision = await check_admin_or_private(
            cfg,
            msg,
            missing_sender="cannot verify sender for trigger settings.",
            failed_member="failed to verify trigger permissions.",
            denied="changing trigger mode is restricted to group admins.",
        )
        if not decision.allowed:
            return ActionPlan(reply_text=decision.error_text or TRIGGER_USAGE)
        if tkey is not None:
            if topic_store is None:
                return ActionPlan(reply_text="topic trigger settings are unavailable.")
            return ActionPlan(
                reply_text="topic trigger mode cleared (using chat default).",
                actions=(lambda: topic_store.clear_trigger_mode(tkey[0], tkey[1]),),
            )
        if chat_prefs is None:
            return ActionPlan(
                reply_text="chat trigger settings are unavailable (no config path)."
            )
        return ActionPlan(
            reply_text="chat trigger mode reset to `all`.",
            actions=(lambda: chat_prefs.clear_trigger_mode(msg.chat_id),),
        )

    return ActionPlan(reply_text=TRIGGER_USAGE)
