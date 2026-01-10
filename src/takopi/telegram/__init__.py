"""Telegram-specific clients and adapters."""

from .client import parse_incoming_update, poll_incoming
from .types import (
    TelegramCallbackQuery,
    TelegramDocument,
    TelegramIncomingMessage,
    TelegramIncomingUpdate,
    TelegramVoice,
)

__all__ = [
    "TelegramCallbackQuery",
    "TelegramDocument",
    "TelegramIncomingMessage",
    "TelegramIncomingUpdate",
    "TelegramVoice",
    "parse_incoming_update",
    "poll_incoming",
]
