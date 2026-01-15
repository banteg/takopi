"""Hooks system for running external code at specific lifecycle points.

Hooks enable access control, logging, custom workflows, and integrations
without modifying takopi core.

Each hook can implement any combination of:
- pre_session: Called before engine runs, can block execution
- post_session: Called after engine completes (fire-and-forget)
- on_error: Called when an error occurs (fire-and-forget)
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Iterable
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from typing import Any, Literal, Protocol, runtime_checkable

from .logging import get_logger
from .model import EngineId
from .plugins import (
    PluginLoadError,
    PluginLoadFailed,
    entrypoint_distribution_name,
    is_entrypoint_allowed,
    normalize_allowlist,
)

logger = get_logger(__name__)

__all__ = [
    "PreSessionContext",
    "PreSessionResult",
    "PostSessionContext",
    "PostSessionResult",
    "OnErrorContext",
    "OnErrorResult",
    "Hook",
    "HookRegistry",
    "HooksConfig",
    "run_pre_session_hooks",
    "run_post_session_hooks",
    "run_on_error_hooks",
]

HOOK_GROUP = "takopi.hooks"


# -----------------------------------------------------------------------------
# Hook Contexts and Results
# -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PreSessionContext:
    """Context passed to pre_session hooks."""

    sender_id: int | None
    chat_id: int
    thread_id: int | None
    message_text: str
    engine: EngineId | None
    project: str | None
    raw_message: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PreSessionResult:
    """Result from a pre_session hook."""

    allow: bool
    reason: str | None = None
    silent: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PostSessionContext:
    """Context passed to post_session hooks."""

    sender_id: int | None
    chat_id: int
    thread_id: int | None
    engine: EngineId
    project: str | None
    duration_ms: int
    tokens_in: int
    tokens_out: int
    status: Literal["success", "error", "cancelled"]
    error: str | None
    message_text: str | None = None
    response_text: str | None = None
    pre_session_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PostSessionResult:
    """Result from a post_session hook (fire-and-forget, no return needed)."""

    pass


@dataclass(frozen=True, slots=True)
class OnErrorContext:
    """Context passed to on_error hooks."""

    sender_id: int | None
    chat_id: int
    thread_id: int | None
    engine: EngineId | None
    project: str | None
    error_type: str
    error_message: str
    traceback: str | None = None
    pre_session_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class OnErrorResult:
    """Result from an on_error hook (fire-and-forget, no return needed)."""

    pass


# -----------------------------------------------------------------------------
# Hook Protocol
# -----------------------------------------------------------------------------


@runtime_checkable
class Hook(Protocol):
    """Protocol for hook implementations.

    Hooks can implement any combination of these methods.
    Each method is optional - only implement what you need.
    """

    def pre_session(
        self, ctx: PreSessionContext, config: dict[str, Any]
    ) -> PreSessionResult | Awaitable[PreSessionResult]: ...

    def post_session(
        self, ctx: PostSessionContext, config: dict[str, Any]
    ) -> PostSessionResult | None | Awaitable[PostSessionResult | None]: ...

    def on_error(
        self, ctx: OnErrorContext, config: dict[str, Any]
    ) -> OnErrorResult | None | Awaitable[OnErrorResult | None]: ...


# -----------------------------------------------------------------------------
# Hook Configuration
# -----------------------------------------------------------------------------


@dataclass(slots=True)
class HooksConfig:
    """Configuration for hooks."""

    hooks: list[str] = field(default_factory=list)
    pre_session_timeout_ms: int = 1000
    post_session_timeout_ms: int = 5000
    on_error_timeout_ms: int = 5000
    fail_closed: bool = False
    config: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HooksConfig:
        """Create HooksConfig from a dictionary."""
        hooks = data.get("hooks", [])
        if isinstance(hooks, str):
            hooks = [hooks]

        return cls(
            hooks=hooks,
            pre_session_timeout_ms=data.get("pre_session_timeout_ms", 1000),
            post_session_timeout_ms=data.get("post_session_timeout_ms", 5000),
            on_error_timeout_ms=data.get("on_error_timeout_ms", 5000),
            fail_closed=data.get("fail_closed", False),
            config=data.get("config", {}),
        )


# -----------------------------------------------------------------------------
# Hook Registry
# -----------------------------------------------------------------------------


def _is_shell_command(ref: str) -> bool:
    """Check if a hook reference is a shell command."""
    return " " in ref or ref.startswith("/") or ref.startswith("./")


@dataclass(slots=True)
class LoadedHook:
    """A loaded hook ready for execution."""

    ref: str
    hook_obj: Any | None  # Hook instance or callable
    is_shell: bool
    plugin_id: str | None

    def has_pre_session(self) -> bool:
        """Check if this hook handles pre_session."""
        if self.is_shell:
            return True  # Shell hooks handle all event types
        return self.hook_obj is not None and hasattr(self.hook_obj, "pre_session")

    def has_post_session(self) -> bool:
        """Check if this hook handles post_session."""
        if self.is_shell:
            return True
        return self.hook_obj is not None and hasattr(self.hook_obj, "post_session")

    def has_on_error(self) -> bool:
        """Check if this hook handles on_error."""
        if self.is_shell:
            return True
        return self.hook_obj is not None and hasattr(self.hook_obj, "on_error")


class HookRegistry:
    """Registry for loading and managing hooks."""

    def __init__(
        self,
        *,
        allowlist: Iterable[str] | None = None,
    ) -> None:
        self._allowlist = normalize_allowlist(allowlist)
        self._loaded: dict[str, LoadedHook] = {}
        self._load_errors: list[PluginLoadError] = []

    def _load_entrypoint_hook(self, plugin_id: str) -> Any:
        """Load a hook from an entrypoint."""
        eps = list(entry_points().select(group=HOOK_GROUP))

        for ep in eps:
            if not is_entrypoint_allowed(ep, self._allowlist):
                continue

            if ep.name == plugin_id:
                try:
                    hook_factory = ep.load()
                    # If it's a class, instantiate it; if callable, call it
                    if isinstance(hook_factory, type):
                        return hook_factory()
                    return hook_factory
                except Exception as exc:
                    error = PluginLoadError(
                        group=HOOK_GROUP,
                        name=ep.name,
                        value=ep.value,
                        distribution=entrypoint_distribution_name(ep),
                        error=str(exc),
                    )
                    self._load_errors.append(error)
                    raise PluginLoadFailed(error) from exc

        available = [
            ep.name for ep in eps if is_entrypoint_allowed(ep, self._allowlist)
        ]
        raise LookupError(f"Hook '{plugin_id}' not found. Available: {available}")

    def load_hook(self, ref: str) -> LoadedHook:
        """Load a hook by reference."""
        if ref in self._loaded:
            return self._loaded[ref]

        if _is_shell_command(ref):
            loaded = LoadedHook(
                ref=ref,
                hook_obj=None,
                is_shell=True,
                plugin_id=None,
            )
        else:
            try:
                hook_obj = self._load_entrypoint_hook(ref)
                loaded = LoadedHook(
                    ref=ref,
                    hook_obj=hook_obj,
                    is_shell=False,
                    plugin_id=ref,
                )
            except (LookupError, PluginLoadFailed):
                raise

        self._loaded[ref] = loaded
        return loaded

    def get_load_errors(self) -> list[PluginLoadError]:
        """Return any errors encountered during hook loading."""
        return list(self._load_errors)


# -----------------------------------------------------------------------------
# Shell Command Execution
# -----------------------------------------------------------------------------


type HookContext = PreSessionContext | PostSessionContext | OnErrorContext


def _context_to_json(ctx: HookContext) -> str:
    """Serialize a context to JSON for shell commands."""
    if isinstance(ctx, PreSessionContext):
        data = {
            "type": "pre_session",
            "sender_id": ctx.sender_id,
            "chat_id": ctx.chat_id,
            "thread_id": ctx.thread_id,
            "message_text": ctx.message_text,
            "engine": ctx.engine,
            "project": ctx.project,
            "raw_message": ctx.raw_message,
        }
    elif isinstance(ctx, PostSessionContext):
        data = {
            "type": "post_session",
            "sender_id": ctx.sender_id,
            "chat_id": ctx.chat_id,
            "thread_id": ctx.thread_id,
            "engine": ctx.engine,
            "project": ctx.project,
            "duration_ms": ctx.duration_ms,
            "tokens_in": ctx.tokens_in,
            "tokens_out": ctx.tokens_out,
            "status": ctx.status,
            "error": ctx.error,
            "message_text": ctx.message_text,
            "response_text": ctx.response_text,
            "pre_session_metadata": ctx.pre_session_metadata,
        }
    else:  # OnErrorContext
        data = {
            "type": "on_error",
            "sender_id": ctx.sender_id,
            "chat_id": ctx.chat_id,
            "thread_id": ctx.thread_id,
            "engine": ctx.engine,
            "project": ctx.project,
            "error_type": ctx.error_type,
            "error_message": ctx.error_message,
            "traceback": ctx.traceback,
            "pre_session_metadata": ctx.pre_session_metadata,
        }
    return json.dumps(data)


def _parse_pre_session_result(output: str) -> PreSessionResult:
    """Parse shell command output into PreSessionResult."""
    try:
        data = json.loads(output)
        return PreSessionResult(
            allow=data.get("allow", True),
            reason=data.get("reason"),
            silent=data.get("silent", False),
            metadata=data.get("metadata", {}),
        )
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        logger.warning("hook.shell.parse_error", error=str(exc), output=output[:200])
        return PreSessionResult(allow=True)


async def _run_shell_hook(
    command: str,
    ctx: HookContext,
    *,
    timeout_ms: int,
) -> str | None:
    """Run a shell command hook.

    Returns stdout on success, None on error.
    """
    input_json = _context_to_json(ctx)
    timeout_s = timeout_ms / 1000.0

    try:
        process = await asyncio.create_subprocess_shell(
            command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(input_json.encode()),
                timeout=timeout_s,
            )
        except TimeoutError:
            process.kill()
            await process.wait()
            logger.warning(
                "hook.shell.timeout",
                command=command[:100],
                timeout_ms=timeout_ms,
            )
            return None

        if process.returncode != 0:
            logger.warning(
                "hook.shell.failed",
                command=command[:100],
                returncode=process.returncode,
                stderr=stderr.decode()[:500] if stderr else None,
            )
            return None

        return stdout.decode() if stdout else ""

    except Exception as exc:
        logger.exception(
            "hook.shell.error",
            command=command[:100],
            error=str(exc),
        )
        return None


# -----------------------------------------------------------------------------
# Hook Execution
# -----------------------------------------------------------------------------


async def _run_single_pre_session_hook(
    hook: LoadedHook,
    ctx: PreSessionContext,
    config: dict[str, Any],
    *,
    timeout_ms: int,
) -> PreSessionResult | None:
    """Run a single pre_session hook. Returns None if hook doesn't handle this event."""
    if not hook.has_pre_session():
        return None

    if hook.is_shell:
        output = await _run_shell_hook(hook.ref, ctx, timeout_ms=timeout_ms)
        if output is None:
            return PreSessionResult(allow=True)
        return _parse_pre_session_result(output)

    if hook.hook_obj is None:
        return None

    hook_config = config.get(hook.plugin_id or "", {})

    try:
        result = hook.hook_obj.pre_session(ctx, hook_config)
        if asyncio.iscoroutine(result):
            result = await asyncio.wait_for(result, timeout=timeout_ms / 1000.0)
        if result is None:
            return PreSessionResult(allow=True)
        return result
    except TimeoutError:
        logger.warning(
            "hook.pre_session.timeout",
            hook=hook.ref,
            timeout_ms=timeout_ms,
        )
        return PreSessionResult(allow=True)
    except Exception as exc:
        logger.exception(
            "hook.pre_session.error",
            hook=hook.ref,
            error=str(exc),
        )
        return PreSessionResult(allow=True)


async def run_pre_session_hooks(
    registry: HookRegistry,
    hooks_config: HooksConfig,
    ctx: PreSessionContext,
) -> PreSessionResult:
    """Run all hooks' pre_session methods in order.

    Returns first rejection, or allow if all pass.
    On hook error, allows by default (fail-open) unless fail_closed is True.
    """
    combined_metadata: dict[str, Any] = {}

    for ref in hooks_config.hooks:
        try:
            hook = registry.load_hook(ref)
        except (LookupError, PluginLoadFailed) as exc:
            logger.error("hook.pre_session.load_failed", hook=ref, error=str(exc))
            if hooks_config.fail_closed:
                return PreSessionResult(
                    allow=False,
                    reason=f"Hook {ref} failed to load",
                )
            continue

        result = await _run_single_pre_session_hook(
            hook,
            ctx,
            hooks_config.config,
            timeout_ms=hooks_config.pre_session_timeout_ms,
        )

        if result is None:
            # Hook doesn't handle pre_session
            continue

        # Merge metadata from all hooks
        combined_metadata.update(result.metadata)

        if not result.allow:
            result.metadata = combined_metadata
            logger.info(
                "hook.pre_session.rejected",
                hook=ref,
                reason=result.reason,
                silent=result.silent,
            )
            return result

    return PreSessionResult(allow=True, metadata=combined_metadata)


async def _run_single_post_session_hook(
    hook: LoadedHook,
    ctx: PostSessionContext,
    config: dict[str, Any],
    *,
    timeout_ms: int,
) -> None:
    """Run a single post_session hook (fire-and-forget)."""
    if not hook.has_post_session():
        return

    if hook.is_shell:
        await _run_shell_hook(hook.ref, ctx, timeout_ms=timeout_ms)
        return

    if hook.hook_obj is None:
        return

    hook_config = config.get(hook.plugin_id or "", {})

    try:
        result = hook.hook_obj.post_session(ctx, hook_config)
        if asyncio.iscoroutine(result):
            await asyncio.wait_for(result, timeout=timeout_ms / 1000.0)
    except TimeoutError:
        logger.warning(
            "hook.post_session.timeout",
            hook=hook.ref,
            timeout_ms=timeout_ms,
        )
    except Exception as exc:
        logger.exception(
            "hook.post_session.error",
            hook=hook.ref,
            error=str(exc),
        )


async def run_post_session_hooks(
    registry: HookRegistry,
    hooks_config: HooksConfig,
    ctx: PostSessionContext,
) -> None:
    """Run all hooks' post_session methods in order (fire-and-forget)."""
    if not hooks_config.hooks:
        return

    for ref in hooks_config.hooks:
        try:
            hook = registry.load_hook(ref)
        except (LookupError, PluginLoadFailed) as exc:
            logger.error("hook.post_session.load_failed", hook=ref, error=str(exc))
            continue

        await _run_single_post_session_hook(
            hook,
            ctx,
            hooks_config.config,
            timeout_ms=hooks_config.post_session_timeout_ms,
        )


async def _run_single_on_error_hook(
    hook: LoadedHook,
    ctx: OnErrorContext,
    config: dict[str, Any],
    *,
    timeout_ms: int,
) -> None:
    """Run a single on_error hook (fire-and-forget)."""
    if not hook.has_on_error():
        return

    if hook.is_shell:
        await _run_shell_hook(hook.ref, ctx, timeout_ms=timeout_ms)
        return

    if hook.hook_obj is None:
        return

    hook_config = config.get(hook.plugin_id or "", {})

    try:
        result = hook.hook_obj.on_error(ctx, hook_config)
        if asyncio.iscoroutine(result):
            await asyncio.wait_for(result, timeout=timeout_ms / 1000.0)
    except TimeoutError:
        logger.warning(
            "hook.on_error.timeout",
            hook=hook.ref,
            timeout_ms=timeout_ms,
        )
    except Exception as exc:
        logger.exception(
            "hook.on_error.error",
            hook=hook.ref,
            error=str(exc),
        )


async def run_on_error_hooks(
    registry: HookRegistry,
    hooks_config: HooksConfig,
    ctx: OnErrorContext,
) -> None:
    """Run all hooks' on_error methods in order (fire-and-forget)."""
    if not hooks_config.hooks:
        return

    for ref in hooks_config.hooks:
        try:
            hook = registry.load_hook(ref)
        except (LookupError, PluginLoadFailed) as exc:
            logger.error("hook.on_error.load_failed", hook=ref, error=str(exc))
            continue

        await _run_single_on_error_hook(
            hook,
            ctx,
            hooks_config.config,
            timeout_ms=hooks_config.on_error_timeout_ms,
        )
