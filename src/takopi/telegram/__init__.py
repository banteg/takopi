"""Telegram-specific clients and adapters."""

from .client import parse_incoming_update, poll_incoming
from .types import TelegramIncomingMessage, TelegramVoice

__all__ = [
    "TelegramIncomingMessage",
    "TelegramVoice",
    "parse_incoming_update",
    "poll_incoming",
]
