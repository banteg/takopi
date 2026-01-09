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
    voice: TelegramVoice | None = None
    raw: dict[str, Any] | None = None
