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
class TelegramDocument:
    file_id: str
    file_name: str | None
    mime_type: str | None
    file_size: int | None
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
    reply_to_is_bot: bool | None = None
    reply_to_username: str | None = None
    media_group_id: str | None = None
    thread_id: int | None = None
    is_topic_message: bool | None = None
    chat_type: str | None = None
    is_forum: bool | None = None
    voice: TelegramVoice | None = None
    document: TelegramDocument | None = None
    raw: dict[str, Any] | None = None

    @property
    def is_private(self) -> bool:
        if self.chat_type is not None:
            return self.chat_type == "private"
        return self.chat_id > 0


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
