# Configuration

Takopi reads configuration from `~/.takopi/takopi.toml`.

If you expect to edit config while Takopi is running, set:

=== "takopi config"

    ```sh
    takopi config set watch_config true
    ```

=== "toml"

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

=== "takopi config"

    ```sh
    takopi config set transports.telegram.bot_token "..."
    takopi config set transports.telegram.chat_id 123
    ```

=== "toml"

    ```toml
    [transports.telegram]
    bot_token = "..."
    chat_id = 123
    ```

| Key | Type | Default | Notes |
|-----|------|---------|-------|
| `bot_token` | string | (required) | Telegram bot token from @BotFather. |
| `chat_id` | int | (required) | Default chat id. |
| `allowed_user_ids` | int[] | `[]` | Allowed sender user ids. Empty disables sender filtering; when set, only these users can interact (including DMs). |
| `message_overflow` | `"trim"`\|`"split"` | `"trim"` | How to handle long final responses. |
| `forward_coalesce_s` | float | `1.0` | Quiet window for combining a prompt with immediately-following forwarded messages; set `0` to disable. |
| `voice_transcription` | bool | `false` | Enable voice note transcription. |
| `voice_max_bytes` | int | `10485760` | Max voice note size (bytes). |
| `voice_transcription_model` | string | `"gpt-4o-mini-transcribe"` | OpenAI transcription model name. |
| `voice_transcription_base_url` | string\|null | `null` | Override base URL for voice transcription only. |
| `voice_transcription_api_key` | string\|null | `null` | Override API key for voice transcription only. |
| `session_mode` | `"stateless"`\|`"chat"` | `"stateless"` | Auto-resume mode. Onboarding sets `"chat"` for assistant/workspace. |
| `show_resume_line` | bool | `true` | Show resume line in message footer. Onboarding sets `false` for assistant/workspace. |

When `allowed_user_ids` is set, updates without a sender id (for example, some channel posts) are ignored.

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
| `allowed_user_ids` | int[] | `[]` | Allowed senders for file transfer; empty allows private chats (group usage requires admin). |
| `deny_globs` | string[] | (defaults) | Glob denylist (e.g. `.git/**`, `**/*.pem`). |

File size limits (not configurable):

- uploads: 20 MiB
- downloads: 50 MiB

## `projects.<alias>`

=== "takopi config"

    ```sh
    takopi config set projects.happy-gadgets.path "~/dev/happy-gadgets"
    takopi config set projects.happy-gadgets.worktrees_dir ".worktrees"
    takopi config set projects.happy-gadgets.default_engine "claude"
    takopi config set projects.happy-gadgets.worktree_base "master"
    takopi config set projects.happy-gadgets.chat_id -1001234567890
    ```

=== "toml"

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

=== "takopi config"

    ```sh
    takopi config set plugins.enabled '["takopi-transport-slack", "takopi-engine-acme"]'
    ```

=== "toml"

    ```toml
    [plugins]
    enabled = ["takopi-transport-slack", "takopi-engine-acme"]
    ```

- `enabled = []` (default) means “load all installed plugins”.
- If non-empty, only distributions with matching names are visible (case-insensitive).

### `plugins.<id>`

Plugin-specific configuration lives under `[plugins.<id>]` and is passed to command plugins as `ctx.plugin_config`.

## Engine-specific config tables

Engines use **top-level tables** keyed by engine id. Built-in engines are listed
here; plugin engines should document their own keys.

### `codex`

| Key | Type | Default | Notes |
|-----|------|---------|-------|
| `extra_args` | string[] | `["-c", "notify=[]"]` | Extra CLI args for `codex` (exec-only flags are rejected). |
| `profile` | string | (unset) | Passed as `--profile <name>` and used as the session title. |

=== "takopi config"

    ```sh
    takopi config set codex.extra_args '["-c", "notify=[]"]'
    takopi config set codex.profile "work"
    ```

=== "toml"

    ```toml
    [codex]
    extra_args = ["-c", "notify=[]"]
    profile = "work"
    ```

### `claude`

| Key | Type | Default | Notes |
|-----|------|---------|-------|
| `model` | string | (unset) | Optional model override. |
| `allowed_tools` | string[] | `["Bash", "Read", "Edit", "Write"]` | Auto-approve tool rules. |
| `dangerously_skip_permissions` | bool | `false` | Skip Claude permissions prompts. |
| `use_api_billing` | bool | `false` | Keep `ANTHROPIC_API_KEY` for API billing. |

=== "takopi config"

    ```sh
    takopi config set claude.model "claude-sonnet-4-5-20250929"
    takopi config set claude.allowed_tools '["Bash", "Read", "Edit", "Write"]'
    takopi config set claude.dangerously_skip_permissions false
    takopi config set claude.use_api_billing false
    ```

=== "toml"

    ```toml
    [claude]
    model = "claude-sonnet-4-5-20250929"
    allowed_tools = ["Bash", "Read", "Edit", "Write"]
    dangerously_skip_permissions = false
    use_api_billing = false
    ```

### `pi`

| Key | Type | Default | Notes |
|-----|------|---------|-------|
| `model` | string | (unset) | Passed as `--model`. |
| `provider` | string | (unset) | Passed as `--provider`. |
| `extra_args` | string[] | `[]` | Extra CLI args for `pi`. |

=== "takopi config"

    ```sh
    takopi config set pi.model "..."
    takopi config set pi.provider "..."
    takopi config set pi.extra_args "[]"
    ```

=== "toml"

    ```toml
    [pi]
    model = "..."
    provider = "..."
    extra_args = []
    ```

### `opencode`

| Key | Type | Default | Notes |
|-----|------|---------|-------|
| `model` | string | (unset) | Optional model override. |

=== "takopi config"

    ```sh
    takopi config set opencode.model "claude-sonnet"
    ```

=== "toml"

    ```toml
    [opencode]
    model = "claude-sonnet"
    ```
