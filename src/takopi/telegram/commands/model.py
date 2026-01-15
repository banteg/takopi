from __future__ import annotations

from typing import TYPE_CHECKING

from ...context import RunContext
from ...directives import DirectiveError
from ..chat_prefs import ChatPrefsStore
from ..engine_defaults import resolve_engine_for_message
from ..engine_overrides import EngineOverrides, resolve_override_value
from ..files import split_command_args
from ..topic_state import TopicStateStore
from ..topics import _topic_key
from ..types import TelegramIncomingMessage
from .reply import make_reply

if TYPE_CHECKING:
    from ..bridge import TelegramBridgeConfig

MODEL_USAGE = (
    "usage: `/model`, `/model set <model>`, "
    "`/model set <engine> <model>`, or `/model clear [engine]`"
)


async def _check_model_permissions(
    cfg: TelegramBridgeConfig, msg: TelegramIncomingMessage
) -> bool:
    reply = make_reply(cfg, msg)
    sender_id = msg.sender_id
    if sender_id is None:
        await reply(text="cannot verify sender for model overrides.")
        return False
    is_private = msg.chat_type == "private"
    if msg.chat_type is None:
        is_private = msg.chat_id > 0
    if is_private:
        return True
    member = await cfg.bot.get_chat_member(msg.chat_id, sender_id)
    if member is None:
        await reply(text="failed to verify model override permissions.")
        return False
    if member.status in {"creator", "administrator"}:
        return True
    await reply(text="changing model overrides is restricted to group admins.")
    return False


async def _resolve_engine_selection(
    cfg: TelegramBridgeConfig,
    msg: TelegramIncomingMessage,
    *,
    ambient_context: RunContext | None,
    topic_store: TopicStateStore | None,
    chat_prefs: ChatPrefsStore | None,
    topic_key: tuple[int, int] | None,
) -> tuple[str, str] | None:
    reply = make_reply(cfg, msg)
    try:
        resolved = cfg.runtime.resolve_message(
            text="",
            reply_text=msg.reply_to_text,
            ambient_context=ambient_context,
            chat_id=msg.chat_id,
        )
    except DirectiveError as exc:
        await reply(text=f"error:\n{exc}")
        return None
    selection = await resolve_engine_for_message(
        runtime=cfg.runtime,
        context=resolved.context,
        explicit_engine=None,
        chat_id=msg.chat_id,
        topic_key=topic_key,
        topic_store=topic_store,
        chat_prefs=chat_prefs,
    )
    return selection.engine, selection.source


def _parse_set_args(
    tokens: tuple[str, ...], *, engine_ids: set[str]
) -> tuple[str | None, str | None]:
    if len(tokens) < 2:
        return None, None
    if len(tokens) == 2:
        maybe_engine = tokens[1].strip().lower()
        if maybe_engine in engine_ids:
            return None, None
        return None, tokens[1].strip()
    maybe_engine = tokens[1].strip().lower()
    if maybe_engine in engine_ids:
        model = " ".join(tokens[2:]).strip()
        return maybe_engine, model or None
    model = " ".join(tokens[1:]).strip()
    return None, model or None


async def _handle_model_command(
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
    engine_ids = {engine.lower() for engine in cfg.runtime.engine_ids}

    if action in {"show", ""}:
        selection = await _resolve_engine_selection(
            cfg,
            msg,
            ambient_context=ambient_context,
            topic_store=topic_store,
            chat_prefs=chat_prefs,
            topic_key=tkey,
        )
        if selection is None:
            return
        engine, engine_source = selection
        topic_override = None
        if tkey is not None and topic_store is not None:
            topic_override = await topic_store.get_engine_override(
                tkey[0], tkey[1], engine
            )
        chat_override = None
        if chat_prefs is not None:
            chat_override = await chat_prefs.get_engine_override(msg.chat_id, engine)
        resolution = resolve_override_value(
            topic_override=topic_override,
            chat_override=chat_override,
            field="model",
        )
        source_labels = {
            "directive": "directive",
            "topic_default": "topic default",
            "chat_default": "chat default",
            "project_default": "project default",
            "global_default": "global default",
        }
        override_labels = {
            "topic_override": "topic override",
            "chat_default": "chat default",
            "default": "no override",
        }
        engine_line = f"engine: {engine} ({source_labels[engine_source]})"
        model_value = resolution.value or "default"
        model_line = f"model: {model_value} ({override_labels[resolution.source]})"
        topic_label = resolution.topic_value or "none"
        if tkey is None:
            topic_label = "none"
        chat_label = (
            "unavailable" if chat_prefs is None else resolution.chat_value or "none"
        )
        defaults_line = f"defaults: topic: {topic_label}, chat: {chat_label}"
        available_line = f"available engines: {', '.join(cfg.runtime.engine_ids)}"
        await reply(
            text="\n\n".join([engine_line, model_line, defaults_line, available_line])
        )
        return

    if action == "set":
        engine_arg, model = _parse_set_args(tokens, engine_ids=engine_ids)
        if model is None:
            await reply(text=MODEL_USAGE)
            return
        if not await _check_model_permissions(cfg, msg):
            return
        if engine_arg is None:
            selection = await _resolve_engine_selection(
                cfg,
                msg,
                ambient_context=ambient_context,
                topic_store=topic_store,
                chat_prefs=chat_prefs,
                topic_key=tkey,
            )
            if selection is None:
                return
            engine, _ = selection
        else:
            engine = engine_arg
            if engine not in engine_ids:
                available = ", ".join(cfg.runtime.engine_ids)
                await reply(
                    text=f"unknown engine `{engine}`.\navailable agents: `{available}`"
                )
                return
        if tkey is not None:
            if topic_store is None:
                await reply(text="topic model overrides are unavailable.")
                return
            current = await topic_store.get_engine_override(tkey[0], tkey[1], engine)
            updated = EngineOverrides(
                model=model,
                reasoning=current.reasoning if current is not None else None,
            )
            await topic_store.set_engine_override(tkey[0], tkey[1], engine, updated)
            await reply(
                text=(
                    f"topic model override set to `{model}` for `{engine}`.\n"
                    "If you want a clean start on the new model, run `/new`."
                )
            )
            return
        if chat_prefs is None:
            await reply(text="chat model overrides are unavailable (no config path).")
            return
        current = await chat_prefs.get_engine_override(msg.chat_id, engine)
        updated = EngineOverrides(
            model=model,
            reasoning=current.reasoning if current is not None else None,
        )
        await chat_prefs.set_engine_override(msg.chat_id, engine, updated)
        await reply(
            text=(
                f"chat model override set to `{model}` for `{engine}`.\n"
                "If you want a clean start on the new model, run `/new`."
            )
        )
        return

    if action == "clear":
        engine = None
        if len(tokens) > 2:
            await reply(text=MODEL_USAGE)
            return
        if len(tokens) == 2:
            engine = tokens[1].strip().lower() or None
        if not await _check_model_permissions(cfg, msg):
            return
        if engine is None:
            selection = await _resolve_engine_selection(
                cfg,
                msg,
                ambient_context=ambient_context,
                topic_store=topic_store,
                chat_prefs=chat_prefs,
                topic_key=tkey,
            )
            if selection is None:
                return
            engine, _ = selection
        if engine not in engine_ids:
            available = ", ".join(cfg.runtime.engine_ids)
            await reply(
                text=f"unknown engine `{engine}`.\navailable agents: `{available}`"
            )
            return
        if tkey is not None:
            if topic_store is None:
                await reply(text="topic model overrides are unavailable.")
                return
            current = await topic_store.get_engine_override(tkey[0], tkey[1], engine)
            updated = EngineOverrides(
                model=None,
                reasoning=current.reasoning if current is not None else None,
            )
            await topic_store.set_engine_override(tkey[0], tkey[1], engine, updated)
            await reply(text="topic model override cleared (using chat default).")
            return
        if chat_prefs is None:
            await reply(text="chat model overrides are unavailable (no config path).")
            return
        current = await chat_prefs.get_engine_override(msg.chat_id, engine)
        updated = EngineOverrides(
            model=None,
            reasoning=current.reasoning if current is not None else None,
        )
        await chat_prefs.set_engine_override(msg.chat_id, engine, updated)
        await reply(text="chat model override cleared.")
        return

    await reply(text=MODEL_USAGE)
