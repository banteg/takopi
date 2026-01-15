# Transport-Agnostic Hooks Refactor

## Context & Goals

Currently, hooks are tightly coupled to the Telegram transport:
- `TelegramHooksManager` lives in `telegram/hooks_integration.py`
- Hook invocation happens in `telegram/loop.py`
- `ResponseCapture` and `_SpyTransport` are in `telegram/commands/executor.py`

**Goal**: Lift hooks into a core session layer so they work identically across any transport (Telegram, CLI, Discord, API).

## Current Architecture

```
src/takopi/
├── hooks.py                          # ✅ Already generic (dataclasses, protocols, runners)
└── telegram/
    ├── hooks_integration.py          # ❌ TelegramHooksManager (Telegram-specific wrapper)
    ├── loop.py                       # ❌ Hook invocation (pre/post/on_error)
    └── commands/executor.py          # ❌ ResponseCapture, _SpyTransport
```

## Target Architecture

```
src/takopi/
├── hooks.py                          # Keep: dataclasses, protocols, shell/plugin hooks
├── session/
│   ├── __init__.py                   # Export SessionManager, contexts
│   ├── manager.py                    # SessionManager - orchestrates hooks + engine
│   ├── contexts.py                   # Transport-agnostic context dataclasses
│   └── capture.py                    # ResponseCapture (moved from executor.py)
└── telegram/
    ├── loop.py                       # Simplified: calls SessionManager
    └── commands/executor.py          # Remove ResponseCapture, _SpyTransport
```

## Implementation Plan

### Phase 1: Create Core Session Module

#### 1.1 Create `src/takopi/session/capture.py`

Move `ResponseCapture` from `telegram/commands/executor.py`:

```python
from dataclasses import dataclass

@dataclass(slots=True)
class ResponseCapture:
    """Mutable container to capture the final response text."""
    text: str | None = None
```

#### 1.2 Create `src/takopi/session/contexts.py`

Generalize context dataclasses with transport-agnostic field names:

```python
from dataclasses import dataclass, field
from typing import Any, Literal

from takopi.engines import EngineId

@dataclass(frozen=True, slots=True)
class SessionIdentity:
    """Transport-agnostic session identifiers."""
    transport: str                    # "telegram", "discord", "cli", etc.
    user_id: str | None               # User identifier (stringified)
    channel_id: str                   # Channel/chat/room identifier
    thread_id: str | None = None      # Optional thread/topic

@dataclass(frozen=True, slots=True)
class PreSessionContext:
    """Context for pre-session hooks."""
    identity: SessionIdentity
    message_text: str
    engine: EngineId | None
    project: str | None
    raw_message: dict[str, Any] = field(default_factory=dict)

@dataclass(frozen=True, slots=True)
class PostSessionContext:
    """Context for post-session hooks."""
    identity: SessionIdentity
    engine: EngineId | None
    project: str | None
    duration_ms: int
    tokens_in: int
    tokens_out: int
    status: Literal["success", "error", "cancelled"]
    error: str | None
    message_text: str | None = None
    response_text: str | None = None
    pre_session_metadata: dict[str, Any] = field(default_factory=dict)

@dataclass(frozen=True, slots=True)
class OnErrorContext:
    """Context for on-error hooks."""
    identity: SessionIdentity
    engine: EngineId | None
    project: str | None
    error_type: str
    error_message: str
    traceback: str | None = None
    pre_session_metadata: dict[str, Any] = field(default_factory=dict)
```

#### 1.3 Create `src/takopi/session/manager.py`

The core `SessionManager` that orchestrates hooks:

```python
from takopi.hooks import HookRegistry, HooksSettings, run_pre_session_hooks, run_post_session_hooks, run_on_error_hooks
from takopi.session.contexts import PreSessionContext, PostSessionContext, OnErrorContext, SessionIdentity

class HooksManager:
    """Transport-agnostic hooks manager."""

    def __init__(self, settings: HooksSettings | None) -> None:
        self._settings = settings
        self._registry: HookRegistry | None = None
        if settings:
            self._registry = HookRegistry(settings)

    @property
    def has_hooks(self) -> bool:
        return self._registry is not None and len(self._registry.hooks) > 0

    async def run_pre_session(self, ctx: PreSessionContext) -> PreSessionResult:
        ...

    async def run_post_session(self, ctx: PostSessionContext) -> None:
        ...

    async def run_on_error(self, ctx: OnErrorContext) -> None:
        ...
```

#### 1.4 Create `src/takopi/session/__init__.py`

```python
from takopi.session.capture import ResponseCapture
from takopi.session.contexts import (
    SessionIdentity,
    PreSessionContext,
    PostSessionContext,
    OnErrorContext,
)
from takopi.session.manager import HooksManager

__all__ = [
    "ResponseCapture",
    "SessionIdentity",
    "PreSessionContext",
    "PostSessionContext",
    "OnErrorContext",
    "HooksManager",
]
```

### Phase 2: Update Core Hooks Module

#### 2.1 Update `src/takopi/hooks.py`

- Keep existing `PreSessionResult`, `PostSessionResult`, `OnErrorResult`
- Keep `Hook` protocol, `HookRegistry`, `HooksSettings`
- Keep `run_pre_session_hooks()`, `run_post_session_hooks()`, `run_on_error_hooks()`
- Update `_context_to_json()` to handle new context types
- Deprecate old context dataclasses (or keep for backwards compat with alias)

The hook runner functions need to accept the new context types. Since they use duck typing (calling `_context_to_json()`), they should work with minimal changes.

### Phase 3: Update Telegram Integration

#### 3.1 Update `src/takopi/telegram/hooks_integration.py`

Replace `TelegramHooksManager` with thin adapter that creates `SessionIdentity`:

```python
from takopi.session import HooksManager, SessionIdentity, PreSessionContext, PostSessionContext, OnErrorContext

def create_telegram_identity(
    sender_id: int | None,
    chat_id: int,
    thread_id: int | None,
) -> SessionIdentity:
    """Create SessionIdentity from Telegram message data."""
    return SessionIdentity(
        transport="telegram",
        user_id=str(sender_id) if sender_id else None,
        channel_id=str(chat_id),
        thread_id=str(thread_id) if thread_id else None,
    )

# Keep factory functions but use new context types
def create_pre_session_context(...) -> PreSessionContext:
    identity = create_telegram_identity(sender_id, chat_id, thread_id)
    return PreSessionContext(identity=identity, ...)
```

#### 3.2 Update `src/takopi/telegram/loop.py`

- Import `HooksManager` from `takopi.session` instead of `TelegramHooksManager`
- Import `ResponseCapture` from `takopi.session` instead of `executor`
- Update context creation calls to use new factory functions
- The hook invocation pattern stays the same

#### 3.3 Update `src/takopi/telegram/commands/executor.py`

- Remove `ResponseCapture` (moved to session module)
- Remove `_SpyTransport` (moved to session module or keep here as transport-specific)
- Import `ResponseCapture` from `takopi.session`

Actually, `_SpyTransport` is inherently transport-specific (wraps Telegram transport). Options:
1. Keep `_SpyTransport` in executor.py, just import `ResponseCapture` from session
2. Create a generic spy pattern in session module

**Decision**: Keep `_SpyTransport` in executor.py since it's Telegram-specific. Other transports will implement their own capture mechanism. Only `ResponseCapture` is generic.

### Phase 4: Update Tests

#### 4.1 Update `tests/test_hooks.py`

- Update imports to use new module paths
- Update test contexts to use `SessionIdentity`
- Add tests for `HooksManager` from session module
- Keep backwards compatibility tests if needed

### Phase 5: Documentation

#### 5.1 Update `docs/reference/config.md`

- Document new context structure
- Show `SessionIdentity` fields
- Update hook payload examples

#### 5.2 Update `CHANGELOG.md`

- Document breaking changes to hook context structure
- Migration guide for existing hooks

## Migration Notes

### Breaking Changes

1. **Context Structure**: Hook contexts now use `identity: SessionIdentity` instead of flat fields
   - Old: `ctx.sender_id`, `ctx.chat_id`, `ctx.thread_id`
   - New: `ctx.identity.user_id`, `ctx.identity.channel_id`, `ctx.identity.thread_id`

2. **ID Types**: IDs are now strings instead of integers
   - Old: `sender_id: int | None`, `chat_id: int`
   - New: `user_id: str | None`, `channel_id: str`

3. **New Field**: `identity.transport` indicates source ("telegram", etc.)

### Shell Hook JSON Changes

Old format:
```json
{
  "sender_id": 123456789,
  "chat_id": -100987654321,
  "thread_id": null,
  "message_text": "hello"
}
```

New format:
```json
{
  "identity": {
    "transport": "telegram",
    "user_id": "123456789",
    "channel_id": "-100987654321",
    "thread_id": null
  },
  "message_text": "hello"
}
```

### Backwards Compatibility Option

Could add compat layer that flattens identity fields for shell hooks:
```json
{
  "identity": {...},
  "sender_id": 123456789,  // Deprecated alias
  "chat_id": -100987654321,  // Deprecated alias
  ...
}
```

## File Changes Summary

| File | Action |
|------|--------|
| `src/takopi/session/__init__.py` | Create |
| `src/takopi/session/capture.py` | Create |
| `src/takopi/session/contexts.py` | Create |
| `src/takopi/session/manager.py` | Create |
| `src/takopi/hooks.py` | Update `_context_to_json()` |
| `src/takopi/telegram/hooks_integration.py` | Refactor to use session module |
| `src/takopi/telegram/loop.py` | Update imports |
| `src/takopi/telegram/commands/executor.py` | Remove ResponseCapture, update imports |
| `tests/test_hooks.py` | Update for new context structure |
| `docs/reference/config.md` | Update hook payload docs |
| `CHANGELOG.md` | Document changes |

## Acceptance Criteria

- [x] Hooks work identically for Telegram transport
- [x] All existing tests pass (with updates for new structure)
- [x] Shell hooks receive new JSON format
- [x] `ResponseCapture` is importable from `takopi.session`
- [x] `HooksManager` is transport-agnostic
- [x] Documentation updated with new context structure
- [x] Easy path to add new transports (CLI, Discord, etc.)

## Risks

1. **Breaking shell hooks**: Existing shell hooks expect old JSON format
   - Mitigation: Add backwards compat aliases in JSON output

2. **Performance**: Additional indirection through SessionIdentity
   - Mitigation: Minimal, dataclasses are efficient

3. **Complexity**: More modules to maintain
   - Mitigation: Clear separation of concerns, better long-term

## Implementation Summary

### New Files Created

- `src/takopi/session/__init__.py` - Module exports
- `src/takopi/session/capture.py` - `ResponseCapture` dataclass
- `src/takopi/session/contexts.py` - `SessionIdentity`, `PreSessionContext`, `PostSessionContext`, `OnErrorContext`
- `src/takopi/session/manager.py` - `HooksManager` class

### Files Modified

- `src/takopi/hooks.py` - Updated `_context_to_json()` to support both legacy and new contexts via duck typing
- `src/takopi/telegram/hooks_integration.py` - Simplified to use session module, added `create_telegram_identity()` helper
- `src/takopi/telegram/loop.py` - Updated imports to use `ResponseCapture` from session module
- `src/takopi/telegram/commands/executor.py` - Removed `ResponseCapture`, imports from session module
- `tests/test_hooks.py` - Added tests for new context structure and SessionIdentity
- `docs/reference/config.md` - Updated JSON examples to show `identity` object
- `changelog.md` - Documented breaking changes

### Key Design Decisions

1. **Backwards compatibility**: New contexts expose both `identity` object and flat properties (`sender_id`, `chat_id`, `thread_id`) via Python properties
2. **Duck typing in serialization**: `_context_to_json()` uses `hasattr` checks to detect context type, enabling both legacy and new contexts
3. **String IDs in identity**: Transport-agnostic IDs are strings (e.g., `"123"`) while backwards-compat properties return integers
4. **Transport field**: `identity.transport` identifies the source (e.g., `"telegram"`, `"discord"`, `"cli"`)
5. **TelegramHooksManager alias**: Kept for backwards compatibility, now just points to generic `HooksManager`
