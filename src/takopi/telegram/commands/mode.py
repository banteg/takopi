from __future__ import annotations

from typing import TYPE_CHECKING

from ...context import RunContext
from ..chat_prefs import ChatPrefsStore
from ..engine_overrides import EngineOverrides, resolve_override_value
from ..files import split_command_args
from ..topic_state import TopicStateStore
from ..topics import _topic_key
from ..types import TelegramIncomingMessage
from .overrides import (
    ENGINE_SOURCE_LABELS,
    OVERRIDE_SOURCE_LABELS,
    apply_engine_override,
    require_admin_or_private,
    resolve_engine_selection,
)
from .reply import make_reply

if TYPE_CHECKING:
    from ..bridge import TelegramBridgeConfig

MODE_USAGE = "usage: `/mode`, `/mode <name>`, `/mode set <name>`, or `/mode clear`"


def _known_modes_for_engine(cfg: TelegramBridgeConfig, engine: str) -> tuple[str, ...]:
    return cfg.mode_known_modes.get(engine, ())


def _supports_mode_overrides(cfg: TelegramBridgeConfig, engine: str) -> bool:
    return engine in cfg.mode_supported_engines


def _validate_mode_name(
    cfg: TelegramBridgeConfig, *, engine: str, mode: str
) -> tuple[str | None, str | None]:
    normalized = mode.strip().lower()
    if not normalized:
        return None, MODE_USAGE
    known = _known_modes_for_engine(cfg, engine)
    if known and normalized not in known:
        available = ", ".join(known)
        return (
            None,
            f"unknown mode `{normalized}` for `{engine}`.\navailable modes: `{available}`",
        )
    return normalized, None


async def _set_mode_for_message(
    cfg: TelegramBridgeConfig,
    msg: TelegramIncomingMessage,
    *,
    mode: str,
    ambient_context: RunContext | None,
    topic_store: TopicStateStore | None,
    chat_prefs: ChatPrefsStore | None,
    scope_chat_ids: frozenset[int] | None,
    announce: bool,
) -> bool:
    reply = make_reply(cfg, msg)
    tkey = (
        _topic_key(msg, cfg, scope_chat_ids=scope_chat_ids)
        if topic_store is not None
        else None
    )
    selection = await resolve_engine_selection(
        cfg,
        msg,
        ambient_context=ambient_context,
        topic_store=topic_store,
        chat_prefs=chat_prefs,
        topic_key=tkey,
    )
    if selection is None:
        return False
    engine, _engine_source = selection
    if not _supports_mode_overrides(cfg, engine):
        await reply(text=f"engine `{engine}` does not support mode overrides.")
        return False
    normalized_mode, validation_error = _validate_mode_name(
        cfg,
        engine=engine,
        mode=mode,
    )
    if validation_error is not None:
        await reply(text=validation_error)
        return False
    assert normalized_mode is not None
    if not await require_admin_or_private(
        cfg,
        msg,
        missing_sender="cannot verify sender for mode overrides.",
        failed_member="failed to verify mode override permissions.",
        denied="changing mode overrides is restricted to group admins.",
    ):
        return False
    scope = await apply_engine_override(
        reply=reply,
        tkey=tkey,
        topic_store=topic_store,
        chat_prefs=chat_prefs,
        chat_id=msg.chat_id,
        engine=engine,
        update=lambda current: EngineOverrides(
            model=current.model if current is not None else None,
            reasoning=current.reasoning if current is not None else None,
            mode=normalized_mode,
        ),
        topic_unavailable="topic mode overrides are unavailable.",
        chat_unavailable="chat mode overrides are unavailable (no config path).",
    )
    if scope is None:
        return False
    if not announce:
        return True
    if scope == "topic":
        await reply(
            text=(
                f"topic mode override set to `{normalized_mode}` for `{engine}`.\n"
                "If you want a clean start on the new mode, run `/new`."
            )
        )
        return True
    await reply(
        text=(
            f"chat mode override set to `{normalized_mode}` for `{engine}`.\n"
            "If you want a clean start on the new mode, run `/new`."
        )
    )
    return True


async def _handle_mode_command(
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
    _ = resolved_scope
    reply = make_reply(cfg, msg)
    tkey = (
        _topic_key(msg, cfg, scope_chat_ids=scope_chat_ids)
        if topic_store is not None
        else None
    )
    tokens = split_command_args(args_text)
    action = tokens[0].lower() if tokens else "show"

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
            field="mode",
        )
        engine_line = f"engine: {engine} ({ENGINE_SOURCE_LABELS[engine_source]})"
        mode_value = resolution.value or "default"
        mode_line = f"mode: {mode_value} ({OVERRIDE_SOURCE_LABELS[resolution.source]})"
        topic_label = resolution.topic_value or "none"
        if tkey is None:
            topic_label = "none"
        chat_label = (
            "unavailable" if chat_prefs is None else resolution.chat_value or "none"
        )
        defaults_line = f"defaults: topic: {topic_label}, chat: {chat_label}"
        if not _supports_mode_overrides(cfg, engine):
            available_line = "available modes: not supported"
        else:
            known = _known_modes_for_engine(cfg, engine)
            if known:
                available_line = f"available modes: {', '.join(known)}"
            else:
                available_line = "available modes: custom (list unavailable)"
        await reply(
            text="\n\n".join([engine_line, mode_line, defaults_line, available_line])
        )
        return

    if action == "clear":
        if len(tokens) > 1:
            await reply(text=MODE_USAGE)
            return
        if not await require_admin_or_private(
            cfg,
            msg,
            missing_sender="cannot verify sender for mode overrides.",
            failed_member="failed to verify mode override permissions.",
            denied="changing mode overrides is restricted to group admins.",
        ):
            return
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
        engine, _engine_source = selection
        if not _supports_mode_overrides(cfg, engine):
            await reply(text=f"engine `{engine}` does not support mode overrides.")
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
                reasoning=current.reasoning if current is not None else None,
                mode=None,
            ),
            topic_unavailable="topic mode overrides are unavailable.",
            chat_unavailable="chat mode overrides are unavailable (no config path).",
        )
        if scope is None:
            return
        if scope == "topic":
            await reply(text="topic mode override cleared (using chat default).")
            return
        await reply(text="chat mode override cleared.")
        return

    mode = ""
    if action == "set":
        if len(tokens) != 2:
            await reply(text=MODE_USAGE)
            return
        mode = tokens[1]
    else:
        if len(tokens) != 1:
            await reply(text=MODE_USAGE)
            return
        mode = tokens[0]
    await _set_mode_for_message(
        cfg,
        msg,
        mode=mode,
        ambient_context=ambient_context,
        topic_store=topic_store,
        chat_prefs=chat_prefs,
        scope_chat_ids=scope_chat_ids,
        announce=True,
    )
