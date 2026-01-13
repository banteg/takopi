# Feature: require_explicit_trigger mode for Telegram

**Issue:** https://github.com/banteg/takopi/issues/117

## Problem

In group chats or busy channels, takopi responds to every message, which can be noisy. Users want takopi to only respond when explicitly invoked.

## Solution

Add a `require_explicit_trigger` setting to `[transports.telegram]` that, when enabled, ignores messages unless they match one of the explicit triggers.

## Configuration

```toml
[transports.telegram]
require_explicit_trigger = false  # default: process all messages (current behavior)
```

## Explicit Triggers (Messages That Pass Through)

When `require_explicit_trigger = true`, only these message types are processed:

1. **Engine directives** - `/claude`, `/codex`, `/opencode`, `/pi` (or other registered engines)
2. **Project directives** - `/project-alias` as defined in `[projects.*]`
3. **Built-in commands** - `/cancel`, `/new`, `/file`, `/ctx`, `/topic`, `/agent`
4. **Plugin commands** - Commands registered via entrypoints
5. **Replies to takopi messages** - Continue existing conversations (reply to running tasks)

## Implementation Plan

### Task 1: Add configuration setting

**File:** `src/takopi/settings.py`

Add `require_explicit_trigger: bool = False` to `TelegramTransportSettings` class (line ~92).

```python
class TelegramTransportSettings(BaseModel):
    # ... existing fields ...
    require_explicit_trigger: bool = False  # NEW: Only respond to explicit triggers
```

### Task 2: Add directive detection helper

**File:** `src/takopi/directives.py`

Add a lightweight function `has_directive()` that checks if text starts with an engine or project directive, without fully parsing:

```python
def has_directive(
    text: str,
    *,
    engine_ids: tuple[EngineId, ...],
    projects: ProjectsConfig,
) -> bool:
    """Check if text begins with an engine or project directive."""
    # Similar logic to parse_directives but returns bool early
```

### Task 3: Implement filtering logic in telegram loop

**File:** `src/takopi/telegram/loop.py`

After the command parsing block (~line 915), before calling `resolve_message()`, add filtering logic:

```python
# After line 914 (plugin command dispatch), before line 916 (resolve_message)
if cfg.require_explicit_trigger:
    # Check if this message should be processed
    has_trigger = (
        command_id is not None  # Any slash command was parsed
        or _has_directive(text, cfg.runtime)  # Engine/project directive
        or (reply_id is not None and _is_reply_to_bot(reply_id, running_tasks))
    )
    if not has_trigger:
        continue  # Skip this message silently
```

### Task 4: Add helper to check if reply is to bot message

**File:** `src/takopi/telegram/loop.py`

The `running_tasks` dict already tracks bot message IDs. We need to check if `reply_id` is in there:

```python
def _is_reply_to_bot(reply_id: int, running_tasks: RunningTasks, chat_id: int) -> bool:
    """Check if reply_id points to a bot message (running or completed task)."""
    return MessageRef(channel_id=chat_id, message_id=reply_id) in running_tasks
```

Note: Currently `running_tasks` only tracks *running* tasks. We may need to also track completed task message IDs if we want replies to any bot message to work.

### Task 5: Propagate setting to the loop

The `cfg` object in the loop is `TelegramBridgeConfig`. Need to ensure `require_explicit_trigger` is accessible there.

**File:** `src/takopi/telegram/bridge.py` (or wherever TelegramBridgeConfig is defined)

## Files to Modify

1. `src/takopi/settings.py` - Add setting
2. `src/takopi/directives.py` - Add `has_directive()` helper
3. `src/takopi/telegram/loop.py` - Add filtering logic
4. `src/takopi/telegram/bridge.py` - Ensure config propagation (if needed)

## Testing

- Test with `require_explicit_trigger = false` (default) - all messages processed
- Test with `require_explicit_trigger = true`:
  - `/claude prompt` → processed
  - `/myproject prompt` → processed
  - `/cancel` → processed
  - `/file` → processed
  - `plain message` → ignored
  - Reply to bot message → processed

## Risks

- If `running_tasks` doesn't persist completed task IDs, replies to completed tasks won't trigger. This may require additional tracking.
- Need to ensure document uploads still work when they have directives.

## Success Criteria

- Setting is documented and defaults to `false`
- Existing behavior unchanged when `require_explicit_trigger = false`
- When enabled, only explicit triggers cause bot response
- No errors or crashes when filtering messages
