# Configuration

Takopi reads configuration from `~/.takopi/takopi.toml`.

If you expect to edit config while Takopi is running, set:

```toml
watch_config = true
```

## Top-level keys

| Key | Type | Default | Notes |
|-----|------|---------|-------|
| `watch_config` | bool | `false` | Hot-reload config changes (transport excluded). |
| `default_engine` | string | `"codex"` | Default engine id for new threads. |
| `default_project` | string\|null | `null` | Default project alias. |
| `transport` | string | `"telegram"` | Transport backend id. |

## `transports.telegram`

```toml
[transports.telegram]
bot_token = "..."
chat_id = 123
```

| Key | Type | Default | Notes |
|-----|------|---------|-------|
| `bot_token` | string | (required) | Telegram bot token from @BotFather. |
| `chat_id` | int | (required) | Default chat id. |
| `message_overflow` | `"trim"`\|`"split"` | `"trim"` | How to handle long final responses. |
| `voice_transcription` | bool | `false` | Enable voice note transcription. |
| `voice_max_bytes` | int | `10485760` | Max voice note size (bytes). |
| `voice_transcription_model` | string | `"gpt-4o-mini-transcribe"` | OpenAI transcription model name. |
| `session_mode` | `"stateless"`\|`"chat"` | `"stateless"` | Auto-resume mode. Onboarding sets `"chat"` for assistant/workspace. |
| `show_resume_line` | bool | `true` | Show resume line in message footer. Onboarding sets `false` for assistant/workspace. |

### `transports.telegram.topics`

| Key | Type | Default | Notes |
|-----|------|---------|-------|
| `enabled` | bool | `false` | Enable forum-topic features. |
| `scope` | `"auto"`\|`"main"`\|`"projects"`\|`"all"` | `"auto"` | Where topics are managed. |

### `transports.telegram.files`

| Key | Type | Default | Notes |
|-----|------|---------|-------|
| `enabled` | bool | `false` | Enable `/file put` and `/file get`. |
| `auto_put` | bool | `true` | Auto-save uploads. |
| `auto_put_mode` | `"upload"`\|`"prompt"` | `"upload"` | Whether uploads also start a run. |
| `uploads_dir` | string | `"incoming"` | Relative path inside the repo/worktree. |
| `allowed_user_ids` | int[] | `[]` | Allowed senders; empty allows private chats (group usage requires admin). |
| `deny_globs` | string[] | (defaults) | Glob denylist (e.g. `.git/**`, `**/*.pem`). |

File size limits (not configurable):

- uploads: 20 MiB
- downloads: 50 MiB

## `projects.<alias>`

```toml
[projects.happy-gadgets]
path = "~/dev/happy-gadgets"
worktrees_dir = ".worktrees"
default_engine = "claude"
worktree_base = "master"
chat_id = -1001234567890
```

| Key | Type | Default | Notes |
|-----|------|---------|-------|
| `path` | string | (required) | Repo root (expands `~`). Relative paths are resolved against the config directory. |
| `worktrees_dir` | string | `".worktrees"` | Worktree root (relative to `path` unless absolute). |
| `default_engine` | string\|null | `null` | Per-project default engine. |
| `worktree_base` | string\|null | `null` | Base branch for new worktrees. |
| `chat_id` | int\|null | `null` | Bind a Telegram chat to this project. |

Legacy config note: top-level `bot_token` / `chat_id` are auto-migrated into `[transports.telegram]` on startup.

## Hooks

Lifecycle hooks run at key points in the session lifecycle: before execution (pre_session), after completion (post_session), and on errors (on_error).

```toml
[hooks]
hooks = ["auth", "logger", "/path/to/script.sh"]
pre_session_timeout_ms = 1000
post_session_timeout_ms = 5000
on_error_timeout_ms = 5000
fail_closed = false
```

| Key | Type | Default | Notes |
|-----|------|---------|-------|
| `hooks` | string\|string[] | `[]` | Hooks to run. Order defines execution order. |
| `pre_session_timeout_ms` | int | `1000` | Timeout for blocking pre_session hooks. |
| `post_session_timeout_ms` | int | `5000` | Timeout for fire-and-forget post_session hooks. |
| `on_error_timeout_ms` | int | `5000` | Timeout for fire-and-forget on_error hooks. |
| `fail_closed` | bool | `false` | Block session if a hook fails to load. |

Each hook in the list can handle any combination of events (pre_session, post_session, on_error). Hooks only receive events for the methods they implement.

### Hook references

A hook can be:

- **Shell command**: Any string containing spaces or starting with `/` or `./` (e.g., `/usr/bin/python hook.py`)
- **Python plugin**: A plugin id registered via the `takopi.hooks` entrypoint group

### Shell hooks

Shell hooks receive JSON on stdin with a `type` field indicating the event type. They should output JSON on stdout for pre_session events.

**Pre-session input:**
```json
{
  "type": "pre_session",
  "sender_id": 123,
  "chat_id": 456,
  "thread_id": 789,
  "message_text": "hello",
  "engine": "codex",
  "project": "myproject",
  "raw_message": {},
  "identity": {
    "transport": "telegram",
    "user_id": "123",
    "channel_id": "456",
    "thread_id": "789"
  }
}
```

The `identity` object provides transport-agnostic session identifiers. The flat fields (`sender_id`, `chat_id`, `thread_id`) are provided for backwards compatibility.

**Pre-session output:**
```json
{
  "allow": true,
  "reason": null,
  "silent": false,
  "metadata": {"key": "value"}
}
```

Set `"allow": false` to block the session. The `reason` is shown to the user unless `silent` is `true`. The `metadata` object is passed to post_session and on_error hooks.

**Post-session input:**
```json
{
  "type": "post_session",
  "sender_id": 123,
  "chat_id": 456,
  "thread_id": 789,
  "engine": "codex",
  "project": "myproject",
  "duration_ms": 1500,
  "tokens_in": 100,
  "tokens_out": 200,
  "status": "success",
  "error": null,
  "message_text": "What is 2+2?",
  "response_text": "The answer is 4.",
  "pre_session_metadata": {"key": "value"},
  "identity": {
    "transport": "telegram",
    "user_id": "123",
    "channel_id": "456",
    "thread_id": "789"
  }
}
```

The `message_text` and `response_text` fields contain the original user input and the agent's final response, enabling post-processing workflows like chaining, review, or summarization.

**On-error input:**
```json
{
  "type": "on_error",
  "sender_id": 123,
  "chat_id": 456,
  "thread_id": 789,
  "engine": "codex",
  "project": "myproject",
  "error_type": "RuntimeError",
  "error_message": "Something went wrong",
  "traceback": "...",
  "pre_session_metadata": {"key": "value"},
  "identity": {
    "transport": "telegram",
    "user_id": "123",
    "channel_id": "456",
    "thread_id": "789"
  }
}
```

Post-session and on_error hooks are fire-and-forget; their output is ignored.

### Transport-agnostic identity

The `identity` object in hook payloads provides transport-agnostic identifiers:

| Field | Type | Description |
|-------|------|-------------|
| `transport` | string | Transport type (e.g., `"telegram"`, `"discord"`, `"cli"`). |
| `user_id` | string\|null | User identifier as a string. |
| `channel_id` | string | Channel/chat/room identifier as a string. |
| `thread_id` | string\|null | Optional thread/topic identifier. |

For backwards compatibility, the flat fields (`sender_id`, `chat_id`, `thread_id`) are also provided with integer values when available. New hooks should prefer using the `identity` object for better portability across transports.

### Python hooks

Python hooks are classes that implement any combination of `pre_session`, `post_session`, and `on_error` methods:

```python
from takopi.hooks import PreSessionResult
from takopi.session import PreSessionContext, PostSessionContext, OnErrorContext

class MyHook:
    def pre_session(self, ctx: PreSessionContext, config: dict) -> PreSessionResult:
        """Called before session starts. Return allow=False to block."""
        # Use ctx.identity for transport-agnostic access
        # Or ctx.sender_id for backwards-compatible integer access
        if ctx.sender_id not in config.get("allowed_users", []):
            return PreSessionResult(allow=False, reason="Not authorized")
        return PreSessionResult(allow=True)

    def post_session(self, ctx: PostSessionContext, config: dict) -> None:
        """Called after session completes (fire-and-forget)."""
        # Access the original message and agent response
        print(f"Transport: {ctx.identity.transport}")
        print(f"User: {ctx.message_text}")
        print(f"Agent: {ctx.response_text}")
        print(f"Completed in {ctx.duration_ms}ms")

    def on_error(self, ctx: OnErrorContext, config: dict) -> None:
        """Called when an error occurs (fire-and-forget)."""
        print(f"Error: {ctx.error_type}: {ctx.error_message}")
```

Context classes provide both the new `identity` attribute and backwards-compatible properties (`sender_id`, `chat_id`, `thread_id`) for easy migration.

Register via `pyproject.toml`:

```toml
[project.entry-points."takopi.hooks"]
my_hook = "my_package:MyHook"
```

### Hook-specific config

```toml
[hooks.config.auth]
allowed_users = [123, 456]

[hooks.config.logger]
endpoint = "https://example.com/log"
```

## Plugins

### `plugins.enabled`

```toml
[plugins]
enabled = ["takopi-transport-slack", "takopi-engine-acme"]
```

- `enabled = []` (default) means “load all installed plugins”.
- If non-empty, only distributions with matching names are visible (case-insensitive).

### `plugins.<id>`

Plugin-specific configuration lives under `[plugins.<id>]` and is passed to command plugins as `ctx.plugin_config`.

## Engine-specific config tables

Engines can have top-level config tables keyed by engine id, for example:

```toml
[codex]
model = "..."
```

The shape is engine-defined.

