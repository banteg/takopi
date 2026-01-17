from __future__ import annotations

from typing import TYPE_CHECKING

from ...context import RunContext
from ..chat_prefs import ChatPrefsStore
from ..engine_overrides import (
    EngineOverrides,
    allowed_reasoning_levels,
    resolve_override_value,
)
from ..files import split_command_args
from ..topic_state import TopicStateStore
from ..topics import _topic_key
from ..types import TelegramIncomingMessage
from .overrides import (
    ENGINE_SOURCE_LABELS,
    OVERRIDE_SOURCE_LABELS,
    apply_engine_override,
    parse_set_args,
    require_admin_or_private,
    resolve_engine_selection,
)
from .reply import make_reply

if TYPE_CHECKING:
    from ..bridge import TelegramBridgeConfig

REASONING_USAGE = (
    "usage: `/reasoning`, `/reasoning set <level>`, "
    "`/reasoning set <engine> <level>`, or `/reasoning clear [engine]`"
)


async def _handle_reasoning_command(
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
        selection = await resolve_engine_selection(
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
            field="reasoning",
        )
        engine_line = f"engine: {engine} ({ENGINE_SOURCE_LABELS[engine_source]})"
        reasoning_value = resolution.value or "default"
        reasoning_line = (
            f"reasoning: {reasoning_value} "
            f"({OVERRIDE_SOURCE_LABELS[resolution.source]})"
        )
        topic_label = resolution.topic_value or "none"
        if tkey is None:
            topic_label = "none"
        chat_label = (
            "unavailable" if chat_prefs is None else resolution.chat_value or "none"
        )
        defaults_line = f"defaults: topic: {topic_label}, chat: {chat_label}"
        available_levels = ", ".join(allowed_reasoning_levels(engine))
        available_line = f"available levels: {available_levels}"
        await reply(
            text="\n\n".join(
                [engine_line, reasoning_line, defaults_line, available_line]
            )
        )
        return

    if action == "set":
        engine_arg, level = parse_set_args(tokens, engine_ids=engine_ids)
        if level is None:
            await reply(text=REASONING_USAGE)
            return
        if not await require_admin_or_private(
            cfg,
            msg,
            missing_sender="cannot verify sender for reasoning overrides.",
            failed_member="failed to verify reasoning override permissions.",
            denied="changing reasoning overrides is restricted to group admins.",
        ):
            return
        if engine_arg is None:
            selection = await resolve_engine_selection(
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
                    text=f"unknown engine `{engine}`.\navailable engines: `{available}`"
                )
                return
        normalized_level = level.strip().lower()
        allowed = allowed_reasoning_levels(engine)
        if normalized_level not in allowed:
            await reply(
                text=(
                    f"unknown reasoning level `{level}`.\n"
                    f"available levels: {', '.join(allowed)}"
                )
            )
            return
        scope = await apply_engine_override(
            reply=reply,
            tkey=tkey,
            topic_store=topic_store,
            chat_prefs=chat_prefs,
            chat_id=msg.chat_id,
            engine=engine,
            update=lambda current: EngineOverrides(
                model=current.model if current is not None else None,
                reasoning=normalized_level,
            ),
            topic_unavailable="topic reasoning overrides are unavailable.",
            chat_unavailable="chat reasoning overrides are unavailable (no config path).",
        )
        if scope is None:
            return
        if scope == "topic":
            await reply(
                text=(
                    f"topic reasoning override set to `{normalized_level}` "
                    f"for `{engine}`.\n"
                    "If you want a clean start on the new setting, run `/new`."
                )
            )
            return
        await reply(
            text=(
                f"chat reasoning override set to `{normalized_level}` for `{engine}`.\n"
                "If you want a clean start on the new setting, run `/new`."
            )
        )
        return

    if action == "clear":
        engine = None
        if len(tokens) > 2:
            await reply(text=REASONING_USAGE)
            return
        if len(tokens) == 2:
            engine = tokens[1].strip().lower() or None
        if not await require_admin_or_private(
            cfg,
            msg,
            missing_sender="cannot verify sender for reasoning overrides.",
            failed_member="failed to verify reasoning override permissions.",
            denied="changing reasoning overrides is restricted to group admins.",
        ):
            return
        if engine is None:
            selection = await resolve_engine_selection(
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
                text=f"unknown engine `{engine}`.\navailable engines: `{available}`"
            )
            return
        scope = await apply_engine_override(
            reply=reply,
            tkey=tkey,
            topic_store=topic_store,
            chat_prefs=chat_prefs,
            chat_id=msg.chat_id,
            engine=engine,
            update=lambda current: EngineOverrides(
                model=current.model if current is not None else None,
                reasoning=None,
            ),
            topic_unavailable="topic reasoning overrides are unavailable.",
            chat_unavailable="chat reasoning overrides are unavailable (no config path).",
        )
        if scope is None:
            return
        if scope == "topic":
            await reply(text="topic reasoning override cleared (using chat default).")
            return
        await reply(text="chat reasoning override cleared.")
        return

    await reply(text=REASONING_USAGE)
