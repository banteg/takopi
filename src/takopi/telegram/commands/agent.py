from __future__ import annotations

from typing import TYPE_CHECKING

from ...context import RunContext
from ...directives import DirectiveError
from ..chat_prefs import ChatPrefsStore
from ..engine_defaults import resolve_engine_for_message
from ..engine_overrides import resolve_override_value
from ..files import split_command_args
from ..topic_state import TopicStateStore
from ..topics import _topic_key
from ..types import TelegramIncomingMessage
from .reply import make_reply

if TYPE_CHECKING:
    from ..bridge import TelegramBridgeConfig

AGENT_USAGE = "usage: `/agent`, `/agent set <engine>`, or `/agent clear`"


async def _check_agent_permissions(
    cfg: TelegramBridgeConfig, msg: TelegramIncomingMessage
) -> bool:
    reply = make_reply(cfg, msg)
    sender_id = msg.sender_id
    if sender_id is None:
        await reply(text="cannot verify sender for engine defaults.")
        return False
    if msg.is_private:
        return True
    member = await cfg.bot.get_chat_member(msg.chat_id, sender_id)
    if member is None:
        await reply(text="failed to verify engine permissions.")
        return False
    if member.status in {"creator", "administrator"}:
        return True
    await reply(text="changing default engines is restricted to group admins.")
    return False


async def _handle_agent_command(
    cfg: TelegramBridgeConfig,
    msg: TelegramIncomingMessage,
    args_text: str,
    ambient_context: RunContext | None,
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
        try:
            resolved = cfg.runtime.resolve_message(
                text="",
                reply_text=msg.reply_to_text,
                ambient_context=ambient_context,
                chat_id=msg.chat_id,
            )
        except DirectiveError as exc:
            await reply(text=f"error:\n{exc}")
            return
        selection = await resolve_engine_for_message(
            runtime=cfg.runtime,
            context=resolved.context,
            explicit_engine=None,
            chat_id=msg.chat_id,
            topic_key=tkey,
            topic_store=topic_store,
            chat_prefs=chat_prefs,
        )
        source_labels = {
            "directive": "directive",
            "topic_default": "topic default",
            "chat_default": "chat default",
            "project_default": "project default",
            "global_default": "global default",
        }
        agent_line = f"engine: {selection.engine} ({source_labels[selection.source]})"
        topic_override = None
        if tkey is not None and topic_store is not None:
            topic_override = await topic_store.get_engine_override(
                tkey[0], tkey[1], selection.engine
            )
        chat_override = None
        if chat_prefs is not None:
            chat_override = await chat_prefs.get_engine_override(
                msg.chat_id, selection.engine
            )
        override_labels = {
            "topic_override": "topic override",
            "chat_default": "chat default",
            "default": "no override",
        }
        model_resolution = resolve_override_value(
            topic_override=topic_override,
            chat_override=chat_override,
            field="model",
        )
        reasoning_resolution = resolve_override_value(
            topic_override=topic_override,
            chat_override=chat_override,
            field="reasoning",
        )
        model_value = model_resolution.value or "default"
        model_line = (
            f"model: {model_value} ({override_labels[model_resolution.source]})"
        )
        reasoning_value = reasoning_resolution.value or "default"
        reasoning_line = (
            "reasoning: "
            f"{reasoning_value} ({override_labels[reasoning_resolution.source]})"
        )
        topic_default = selection.topic_default or "none"
        if tkey is None:
            topic_default = "none"
        if chat_prefs is None:
            chat_default = "unavailable"
        else:
            chat_default = selection.chat_default or "none"
        project_default = (
            selection.project_default
            if selection.project_default is not None
            else "none"
        )
        defaults_line = (
            "defaults: "
            f"topic: {topic_default}, "
            f"chat: {chat_default}, "
            f"project: {project_default}, "
            f"global: {cfg.runtime.default_engine}"
        )
        available = ", ".join(cfg.runtime.engine_ids)
        available_line = f"available: {available}"
        await reply(
            text="\n\n".join(
                [agent_line, model_line, reasoning_line, defaults_line, available_line]
            )
        )
        return

    if action == "set":
        if len(tokens) < 2:
            await reply(text=AGENT_USAGE)
            return
        if not await _check_agent_permissions(cfg, msg):
            return
        engine = tokens[1].strip().lower()
        if engine not in cfg.runtime.engine_ids:
            available = ", ".join(cfg.runtime.engine_ids)
            await reply(
                text=f"unknown engine `{engine}`.\navailable engines: `{available}`",
            )
            return
        if tkey is not None:
            if topic_store is None:
                await reply(text="topic defaults are unavailable.")
                return
            await topic_store.set_default_engine(tkey[0], tkey[1], engine)
            await reply(text=f"topic default engine set to `{engine}`")
            return
        if chat_prefs is None:
            await reply(text="chat defaults are unavailable (no config path).")
            return
        await chat_prefs.set_default_engine(msg.chat_id, engine)
        await reply(text=f"chat default engine set to `{engine}`")
        return

    if action == "clear":
        if not await _check_agent_permissions(cfg, msg):
            return
        if tkey is not None:
            if topic_store is None:
                await reply(text="topic defaults are unavailable.")
                return
            await topic_store.clear_default_engine(tkey[0], tkey[1])
            await reply(text="topic default engine cleared.")
            return
        if chat_prefs is None:
            await reply(text="chat defaults are unavailable (no config path).")
            return
        await chat_prefs.clear_default_engine(msg.chat_id)
        await reply(text="chat default engine cleared.")
        return

    await reply(text=AGENT_USAGE)
