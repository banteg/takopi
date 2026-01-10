from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class TelegramVoice:
    file_id: str
    mime_type: str | None
    file_size: int | None
    duration: int | None
    raw: dict[str, Any]


@dataclass(frozen=True, slots=True)
class TelegramIncomingMessage:
    transport: str
    chat_id: int
    message_id: int
    text: str
    reply_to_message_id: int | None
    reply_to_text: str | None
    sender_id: int | None
    thread_id: int | None = None
    is_topic_message: bool | None = None
    chat_type: str | None = None
    is_forum: bool | None = None
    voice: TelegramVoice | None = None
    raw: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class TelegramCallbackQuery:
    transport: str
    chat_id: int
    message_id: int
    callback_query_id: str
    data: str | None
    sender_id: int | None
    raw: dict[str, Any] | None = None


TelegramIncomingUpdate = TelegramIncomingMessage | TelegramCallbackQuery
