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

