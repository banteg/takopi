from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class TelegramIncomingMessage:
    transport: str
    chat_id: int
    message_id: int
    text: str
    reply_to_message_id: int | None
    reply_to_text: str | None
    sender_id: int | None
    raw: dict[str, Any] | None = None
