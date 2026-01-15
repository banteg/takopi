"""Hook integration for the Telegram transport.

This module provides Telegram-specific helpers for creating session contexts
and wraps the transport-agnostic HooksManager.
"""

from __future__ import annotations

from typing import Any

from ..context import RunContext
from ..model import EngineId
from ..session import (
    HooksManager,
    OnErrorContext,
    PostSessionContext,
    PreSessionContext,
    SessionIdentity,
)

__all__ = [
    "TelegramHooksManager",
    "create_telegram_identity",
    "create_pre_session_context",
    "create_post_session_context",
    "create_on_error_context",
]

# Re-export HooksManager as TelegramHooksManager for backwards compatibility
TelegramHooksManager = HooksManager


def create_telegram_identity(
    *,
    sender_id: int | None,
    chat_id: int,
    thread_id: int | None,
) -> SessionIdentity:
    """Create a SessionIdentity from Telegram message data.

    Args:
        sender_id: Telegram user ID (or None for anonymous).
        chat_id: Telegram chat ID.
        thread_id: Telegram topic/thread ID (or None).

    Returns:
        SessionIdentity with transport="telegram".
    """
    return SessionIdentity(
        transport="telegram",
        user_id=str(sender_id) if sender_id is not None else None,
        channel_id=str(chat_id),
        thread_id=str(thread_id) if thread_id is not None else None,
    )


def create_pre_session_context(
    *,
    sender_id: int | None,
    chat_id: int,
    thread_id: int | None,
    message_text: str,
    engine: EngineId | None,
    context: RunContext | None,
    raw_message: dict[str, Any] | None = None,
) -> PreSessionContext:
    """Create a PreSessionContext for hook execution.

    Args:
        sender_id: Telegram user ID.
        chat_id: Telegram chat ID.
        thread_id: Telegram topic/thread ID.
        message_text: The user's message text.
        engine: The engine that will be used.
        context: Run context with project info.
        raw_message: Raw Telegram message data.

    Returns:
        PreSessionContext ready for hook execution.
    """
    identity = create_telegram_identity(
        sender_id=sender_id,
        chat_id=chat_id,
        thread_id=thread_id,
    )
    return PreSessionContext(
        identity=identity,
        message_text=message_text,
        engine=engine,
        project=context.project if context else None,
        raw_message=raw_message or {},
    )


def create_post_session_context(
    *,
    sender_id: int | None,
    chat_id: int,
    thread_id: int | None,
    engine: EngineId,
    context: RunContext | None,
    duration_ms: int,
    tokens_in: int = 0,
    tokens_out: int = 0,
    status: str,
    error: str | None = None,
    message_text: str | None = None,
    response_text: str | None = None,
    pre_session_metadata: dict[str, Any] | None = None,
) -> PostSessionContext:
    """Create a PostSessionContext for hook execution.

    Args:
        sender_id: Telegram user ID.
        chat_id: Telegram chat ID.
        thread_id: Telegram topic/thread ID.
        engine: The engine that was used.
        context: Run context with project info.
        duration_ms: Session duration in milliseconds.
        tokens_in: Input tokens used.
        tokens_out: Output tokens generated.
        status: Session status ("success", "error", "cancelled").
        error: Error message if status is "error".
        message_text: Original user message.
        response_text: Final response text.
        pre_session_metadata: Metadata from pre_session hooks.

    Returns:
        PostSessionContext ready for hook execution.
    """
    from typing import Literal, cast

    identity = create_telegram_identity(
        sender_id=sender_id,
        chat_id=chat_id,
        thread_id=thread_id,
    )
    return PostSessionContext(
        identity=identity,
        engine=engine,
        project=context.project if context else None,
        duration_ms=duration_ms,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        status=cast(Literal["success", "error", "cancelled"], status),
        error=error,
        message_text=message_text,
        response_text=response_text,
        pre_session_metadata=pre_session_metadata or {},
    )


def create_on_error_context(
    *,
    sender_id: int | None,
    chat_id: int,
    thread_id: int | None,
    engine: EngineId | None,
    context: RunContext | None,
    error_type: str,
    error_message: str,
    traceback: str | None = None,
    pre_session_metadata: dict[str, Any] | None = None,
) -> OnErrorContext:
    """Create an OnErrorContext for hook execution.

    Args:
        sender_id: Telegram user ID.
        chat_id: Telegram chat ID.
        thread_id: Telegram topic/thread ID.
        engine: The engine that was being used.
        context: Run context with project info.
        error_type: The type/class of the error.
        error_message: The error message.
        traceback: Full traceback if available.
        pre_session_metadata: Metadata from pre_session hooks.

    Returns:
        OnErrorContext ready for hook execution.
    """
    identity = create_telegram_identity(
        sender_id=sender_id,
        chat_id=chat_id,
        thread_id=thread_id,
    )
    return OnErrorContext(
        identity=identity,
        engine=engine,
        project=context.project if context else None,
        error_type=error_type,
        error_message=error_message,
        traceback=traceback,
        pre_session_metadata=pre_session_metadata or {},
    )
