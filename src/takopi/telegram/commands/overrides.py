from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from ...context import RunContext
from ...directives import DirectiveError
from ..chat_prefs import ChatPrefsStore
from ..engine_defaults import resolve_engine_for_message
from ..engine_overrides import EngineOverrides
from ..topic_state import TopicStateStore
from ..types import TelegramIncomingMessage
from .reply import make_reply

if TYPE_CHECKING:
    from ..bridge import TelegramBridgeConfig

ENGINE_SOURCE_LABELS = {
    "directive": "directive",
    "topic_default": "topic default",
    "chat_default": "chat default",
    "project_default": "project default",
    "global_default": "global default",
}
OVERRIDE_SOURCE_LABELS = {
    "topic_override": "topic override",
    "chat_default": "chat default",
    "default": "no override",
}


async def require_admin_or_private(
    cfg: TelegramBridgeConfig,
    msg: TelegramIncomingMessage,
    *,
    missing_sender: str,
    failed_member: str,
    denied: str,
) -> bool:
    reply = make_reply(cfg, msg)
    decision = await check_admin_or_private(
        cfg,
        msg,
        missing_sender=missing_sender,
        failed_member=failed_member,
        denied=denied,
    )
    if decision.allowed:
        return True
    if decision.error_text is not None:
        await reply(text=decision.error_text)
    return False


@dataclass(frozen=True, slots=True)
class PermissionDecision:
    allowed: bool
    error_text: str | None = None


async def check_admin_or_private(
    cfg: TelegramBridgeConfig,
    msg: TelegramIncomingMessage,
    *,
    missing_sender: str,
    failed_member: str,
    denied: str,
) -> PermissionDecision:
    sender_id = msg.sender_id
    if sender_id is None:
        return PermissionDecision(allowed=False, error_text=missing_sender)
    if msg.is_private:
        return PermissionDecision(allowed=True)
    member = await cfg.bot.get_chat_member(msg.chat_id, sender_id)
    if member is None:
        return PermissionDecision(allowed=False, error_text=failed_member)
    if member.status in {"creator", "administrator"}:
        return PermissionDecision(allowed=True)
    return PermissionDecision(allowed=False, error_text=denied)


async def resolve_engine_selection(
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


def parse_set_args(
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
        value = " ".join(tokens[2:]).strip()
        return maybe_engine, value or None
    value = " ".join(tokens[1:]).strip()
    return None, value or None


async def apply_engine_override(
    *,
    reply: Callable[..., Awaitable[None]],
    tkey: tuple[int, int] | None,
    topic_store: TopicStateStore | None,
    chat_prefs: ChatPrefsStore | None,
    chat_id: int,
    engine: str,
    update: Callable[[EngineOverrides | None], EngineOverrides],
    topic_unavailable: str,
    chat_unavailable: str,
) -> Literal["topic", "chat"] | None:
    if tkey is not None:
        if topic_store is None:
            await reply(text=topic_unavailable)
            return None
        current = await topic_store.get_engine_override(tkey[0], tkey[1], engine)
        updated = update(current)
        await topic_store.set_engine_override(tkey[0], tkey[1], engine, updated)
        return "topic"
    if chat_prefs is None:
        await reply(text=chat_unavailable)
        return None
    current = await chat_prefs.get_engine_override(chat_id, engine)
    updated = update(current)
    await chat_prefs.set_engine_override(chat_id, engine, updated)
    return "chat"
