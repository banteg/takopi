"""Telegram-specific clients and adapters."""

from .client import parse_incoming_update, poll_incoming

__all__ = ["parse_incoming_update", "poll_incoming"]
