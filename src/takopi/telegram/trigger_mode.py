from __future__ import annotations

from typing import Literal

from ..transport_runtime import TransportRuntime
from .chat_prefs import ChatPrefsStore
from .commands.parse import _parse_slash_command
from .topic_state import TopicStateStore
from .types import TelegramIncomingMessage

TriggerMode = Literal["all", "mentions"]


async def resolve_trigger_mode(
    *,
    chat_id: int,
    thread_id: int | None,
    chat_prefs: ChatPrefsStore | None,
    topic_store: TopicStateStore | None,
) -> TriggerMode:
    if topic_store is not None and thread_id is not None:
        topic_mode = await topic_store.get_trigger_mode(chat_id, thread_id)
        if topic_mode == "mentions":
            return "mentions"
    if chat_prefs is not None:
        chat_mode = await chat_prefs.get_trigger_mode(chat_id)
        if chat_mode == "mentions":
            return "mentions"
    return "all"


def should_trigger_run(
    msg: TelegramIncomingMessage,
    *,
    bot_username: str | None,
    runtime: TransportRuntime,
    command_ids: set[str],
    reserved_chat_commands: set[str],
) -> bool:
    text = msg.text or ""
    lowered = text.lower()
    if bot_username:
        needle = f"@{bot_username}"
        if needle in lowered:
            return True
    implicit_topic_reply = (
        msg.thread_id is not None and msg.reply_to_message_id == msg.thread_id
    )

    if msg.reply_to_is_bot and not implicit_topic_reply:
        return True
    if (
        bot_username
        and msg.reply_to_username
        and msg.reply_to_username.lower() == bot_username
        and not implicit_topic_reply
    ):
        return True
    command_id, _ = _parse_slash_command(text)
    if not command_id:
        return False
    if command_id in reserved_chat_commands or command_id in command_ids:
        return True
    engine_ids = {engine.lower() for engine in runtime.available_engine_ids()}
    if command_id in engine_ids:
        return True
    project_aliases = {alias.lower() for alias in runtime.project_aliases()}
    return command_id in project_aliases
