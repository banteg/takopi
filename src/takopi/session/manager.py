"""Transport-agnostic hooks manager."""

from __future__ import annotations

from typing import Any

from ..hooks import (
    HookRegistry,
    HooksConfig,
    PreSessionResult,
    run_on_error_hooks as _run_on_error_hooks,
    run_post_session_hooks as _run_post_session_hooks,
    run_pre_session_hooks as _run_pre_session_hooks,
)
from ..logging import get_logger
from ..settings import HooksSettings
from .contexts import OnErrorContext, PostSessionContext, PreSessionContext

logger = get_logger(__name__)

__all__ = ["HooksManager"]


def _hooks_config_dict(settings: HooksSettings) -> dict[str, dict[str, Any]]:
    """Build hook config dict from HooksSettings."""
    extra = settings.model_extra or {}
    config = extra.get("config", {})
    if isinstance(config, dict):
        return config
    return {}


class HooksManager:
    """Transport-agnostic hooks manager.

    This manager handles hook execution for any transport. It wraps
    the core hook execution functions and provides a clean interface
    for transports to use.
    """

    def __init__(self, settings: HooksSettings | None) -> None:
        """Initialize the hooks manager.

        Args:
            settings: Hooks configuration settings. If None, hooks are disabled.
        """
        self._settings = settings
        self._registry: HookRegistry | None = None
        self._has_hooks = False

        if settings and settings.hooks:
            self._registry = HookRegistry(allowlist=None)
            self._has_hooks = True

    @property
    def has_hooks(self) -> bool:
        """Return True if any hooks are configured."""
        return self._has_hooks

    def _build_hooks_config(self) -> HooksConfig:
        """Build HooksConfig from settings."""
        if self._settings is None:
            return HooksConfig()

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
        """Run pre_session hooks and return the result.

        Args:
            ctx: Pre-session context with session information.

        Returns:
            PreSessionResult indicating whether to allow or block the session.
        """
        if not self._has_hooks or self._registry is None:
            return PreSessionResult(allow=True)

        return await _run_pre_session_hooks(
            self._registry, self._build_hooks_config(), ctx
        )

    async def run_post_session(
        self,
        ctx: PostSessionContext,
    ) -> None:
        """Run post_session hooks (fire-and-forget).

        Args:
            ctx: Post-session context with session results.
        """
        if not self._has_hooks or self._registry is None:
            return

        await _run_post_session_hooks(self._registry, self._build_hooks_config(), ctx)

    async def run_on_error(
        self,
        ctx: OnErrorContext,
    ) -> None:
        """Run on_error hooks (fire-and-forget).

        Args:
            ctx: On-error context with error information.
        """
        if not self._has_hooks or self._registry is None:
            return

        await _run_on_error_hooks(self._registry, self._build_hooks_config(), ctx)
