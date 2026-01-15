"""Hook integration for the Telegram transport."""

from __future__ import annotations

from typing import Any

from ..context import RunContext
from ..hooks import (
    HookRegistry,
    HooksConfig,
    OnErrorContext,
    PostSessionContext,
    PreSessionContext,
    PreSessionResult,
    run_on_error_hooks,
    run_post_session_hooks,
    run_pre_session_hooks,
)
from ..logging import get_logger
from ..model import EngineId
from ..settings import HooksSettings

logger = get_logger(__name__)

__all__ = [
    "TelegramHooksManager",
    "create_pre_session_context",
    "create_post_session_context",
    "create_on_error_context",
]


def _hooks_config_dict(settings: HooksSettings) -> dict[str, dict[str, Any]]:
    """Build hook config dict from HooksSettings."""
    extra = settings.model_extra or {}
    config = extra.get("config", {})
    if isinstance(config, dict):
        return config
    return {}


class TelegramHooksManager:
    """Manages hook execution for the Telegram transport."""

    def __init__(self, settings: HooksSettings) -> None:
        self._settings = settings
        self._registry = HookRegistry(allowlist=None)
        self._has_hooks = bool(settings.hooks)

    @property
    def has_hooks(self) -> bool:
        """Return True if any hooks are configured."""
        return self._has_hooks

    def _build_hooks_config(self) -> HooksConfig:
        """Build HooksConfig from settings."""
        return HooksConfig(
            hooks=list(self._settings.hooks),
            pre_session_timeout_ms=self._settings.pre_session_timeout_ms,
            post_session_timeout_ms=self._settings.post_session_timeout_ms,
            on_error_timeout_ms=self._settings.on_error_timeout_ms,
            fail_closed=self._settings.fail_closed,
            config=_hooks_config_dict(self._settings),
        )

    async def run_pre_session(
        self,
        ctx: PreSessionContext,
    ) -> PreSessionResult:
        """Run pre_session hooks and return the result."""
        return await run_pre_session_hooks(
            self._registry, self._build_hooks_config(), ctx
        )

    async def run_post_session(
        self,
        ctx: PostSessionContext,
    ) -> None:
        """Run post_session hooks (fire-and-forget)."""
        await run_post_session_hooks(self._registry, self._build_hooks_config(), ctx)

    async def run_on_error(
        self,
        ctx: OnErrorContext,
    ) -> None:
        """Run on_error hooks (fire-and-forget)."""
        await run_on_error_hooks(self._registry, self._build_hooks_config(), ctx)


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
    """Create a PreSessionContext for hook execution."""
    return PreSessionContext(
        sender_id=sender_id,
        chat_id=chat_id,
        thread_id=thread_id,
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
    """Create a PostSessionContext for hook execution."""
    from typing import Literal, cast

    return PostSessionContext(
        sender_id=sender_id,
        chat_id=chat_id,
        thread_id=thread_id,
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
    """Create an OnErrorContext for hook execution."""
    return OnErrorContext(
        sender_id=sender_id,
        chat_id=chat_id,
        thread_id=thread_id,
        engine=engine,
        project=context.project if context else None,
        error_type=error_type,
        error_message=error_message,
        traceback=traceback,
        pre_session_metadata=pre_session_metadata or {},
    )
