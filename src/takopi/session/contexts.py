"""Transport-agnostic session context dataclasses.

These contexts are passed to hooks and provide information about
the current session without being tied to any specific transport.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from ..model import EngineId

__all__ = [
    "SessionIdentity",
    "PreSessionContext",
    "PostSessionContext",
    "OnErrorContext",
]


@dataclass(frozen=True, slots=True)
class SessionIdentity:
    """Transport-agnostic session identifiers.

    This provides a unified way to identify sessions across different
    transports (Telegram, Discord, CLI, API, etc.).
    """

    transport: str
    """Transport type identifier (e.g., "telegram", "discord", "cli")."""

    user_id: str | None
    """User identifier (stringified from transport-specific ID)."""

    channel_id: str
    """Channel/chat/room identifier (stringified)."""

    thread_id: str | None = None
    """Optional thread/topic identifier (stringified)."""


@dataclass(frozen=True, slots=True)
class PreSessionContext:
    """Context passed to pre_session hooks.

    Pre-session hooks can inspect this context and decide whether
    to allow or block the session from proceeding.
    """

    identity: SessionIdentity
    """Transport-agnostic session identifiers."""

    message_text: str
    """The user's message text."""

    engine: EngineId | None
    """The engine that will be used (if resolved)."""

    project: str | None
    """The project context (if any)."""

    raw_message: dict[str, Any] = field(default_factory=dict)
    """Raw transport-specific message data (opaque to hooks)."""

    # Backwards compatibility - expose identity fields directly
    @property
    def sender_id(self) -> int | None:
        """Backwards compat: sender_id as int (Telegram-style)."""
        if self.identity.user_id is None:
            return None
        try:
            return int(self.identity.user_id)
        except ValueError:
            return None

    @property
    def chat_id(self) -> int:
        """Backwards compat: chat_id as int (Telegram-style)."""
        return int(self.identity.channel_id)

    @property
    def thread_id(self) -> int | None:
        """Backwards compat: thread_id as int (Telegram-style)."""
        if self.identity.thread_id is None:
            return None
        try:
            return int(self.identity.thread_id)
        except ValueError:
            return None


@dataclass(frozen=True, slots=True)
class PostSessionContext:
    """Context passed to post_session hooks.

    Post-session hooks receive information about the completed session
    including timing, token usage, and the response.
    """

    identity: SessionIdentity
    """Transport-agnostic session identifiers."""

    engine: EngineId
    """The engine that was used."""

    project: str | None
    """The project context (if any)."""

    duration_ms: int
    """Session duration in milliseconds."""

    tokens_in: int
    """Number of input tokens used."""

    tokens_out: int
    """Number of output tokens generated."""

    status: Literal["success", "error", "cancelled"]
    """Session completion status."""

    error: str | None
    """Error message if status is 'error'."""

    message_text: str | None = None
    """The original user message."""

    response_text: str | None = None
    """The final response text."""

    pre_session_metadata: dict[str, Any] = field(default_factory=dict)
    """Metadata from pre_session hooks."""

    # Backwards compatibility - expose identity fields directly
    @property
    def sender_id(self) -> int | None:
        """Backwards compat: sender_id as int (Telegram-style)."""
        if self.identity.user_id is None:
            return None
        try:
            return int(self.identity.user_id)
        except ValueError:
            return None

    @property
    def chat_id(self) -> int:
        """Backwards compat: chat_id as int (Telegram-style)."""
        return int(self.identity.channel_id)

    @property
    def thread_id(self) -> int | None:
        """Backwards compat: thread_id as int (Telegram-style)."""
        if self.identity.thread_id is None:
            return None
        try:
            return int(self.identity.thread_id)
        except ValueError:
            return None


@dataclass(frozen=True, slots=True)
class OnErrorContext:
    """Context passed to on_error hooks.

    On-error hooks receive information about errors that occurred
    during session execution.
    """

    identity: SessionIdentity
    """Transport-agnostic session identifiers."""

    engine: EngineId | None
    """The engine that was being used (if resolved)."""

    project: str | None
    """The project context (if any)."""

    error_type: str
    """The type/class of the error."""

    error_message: str
    """The error message."""

    traceback: str | None = None
    """Full traceback (if available)."""

    pre_session_metadata: dict[str, Any] = field(default_factory=dict)
    """Metadata from pre_session hooks."""

    # Backwards compatibility - expose identity fields directly
    @property
    def sender_id(self) -> int | None:
        """Backwards compat: sender_id as int (Telegram-style)."""
        if self.identity.user_id is None:
            return None
        try:
            return int(self.identity.user_id)
        except ValueError:
            return None

    @property
    def chat_id(self) -> int:
        """Backwards compat: chat_id as int (Telegram-style)."""
        return int(self.identity.channel_id)

    @property
    def thread_id(self) -> int | None:
        """Backwards compat: thread_id as int (Telegram-style)."""
        if self.identity.thread_id is None:
            return None
        try:
            return int(self.identity.thread_id)
        except ValueError:
            return None
