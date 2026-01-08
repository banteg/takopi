"""Telegram-specific clients and adapters."""

from .client import parse_incoming_update, poll_incoming
from .types import TelegramIncomingMessage

__all__ = [
    "TelegramIncomingMessage",
    "parse_incoming_update",
    "poll_incoming",
]
