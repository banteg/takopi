# User Guide

This guide starts with the simplest possible handoff and gradually layers on
projects, worktrees, and topics. Use it as a path from "just make it work" to a
fully organized multi-repo setup.

## 0. Install and onboard

```sh
uv tool install -U takopi
takopi
```

The first run guides you through:

- creating a Telegram bot token (via @BotFather)
- capturing your `chat_id`
- choosing a default engine

To re-run onboarding (and overwrite config), use `takopi --onboard`.

Config lives at `~/.takopi/takopi.toml`.

## 1. The simplest handoff

1) `cd` into the repo you want to work on
2) run `takopi`
3) send a message to the bot

Takopi streams progress in the chat and sends a final response.

Basics:

- **reply** to a bot message with more instructions to keep going
- click **cancel** or reply to a progress message with `/cancel`  to stop a run

Minimal config looks like this:

```toml
default_engine = "codex"
transport = "telegram"

[transports.telegram]
bot_token = "123456789:ABCdefGHIjklMNOpqrsTUVwxyz"
chat_id = 123456789
```

## 2. Pick engines per message

Prefix with an engine directive:

```
/codex fix the flaky test
/claude summarize the changes
/opencode add logging
/pi sketch a solution
```

Directives are only parsed at the start of the first non-empty line.

## 3. Projects and branches

Register a repo as a project alias:

```sh
takopi init z80
```

Then use:

- `/z80` to pick the project
- `@branch` to run in a worktree for that branch

Example:

```
/z80 @feat/streaming fix the renderer
```

Config example:

```toml
default_project = "z80"

[projects.z80]
path = "~/dev/z80"
worktrees_dir = ".worktrees"
default_engine = "codex"
worktree_base = "master"
```

Takopi adds a `ctx:` footer to messages with project context and uses it when
you reply, so the context sticks without re-typing directives.

## 4. Automatic worktrees

When you use `@branch`, takopi creates (or reuses) a git worktree:

```
<project.path>/<worktrees_dir>/<branch>
```

If you want worktrees outside the repo (to avoid untracked files), set
`worktrees_dir` to an external path, for example:

```toml
worktrees_dir = "~/.takopi/worktrees/z80"
```

## 5. Per-project chat routing

If you want a dedicated Telegram chat per project, set `projects.<alias>.chat_id`.
Messages from that chat default to the project.

```toml
[projects.z80]
path = "~/dev/z80"
chat_id = -123456789
```

Notes:

- `projects.*.chat_id` must be unique.
- It must not match `transports.telegram.chat_id`.

Tip: capture a chat id without full onboarding:

```sh
takopi chat-id
```

If you want to update a project chat id directly:

```sh
takopi chat-id --project z80
```

## 6. Topics

Forum topics let you bind a thread to a project/branch and keep session resumes per-topic.

Enable topics:

```toml
[transports.telegram.topics]
enabled = true
mode = "multi_project_chat" # or "per_project_chat"
```

Commands (inside a topic):

- `/ctx` shows the bound context
- `/ctx set ...` updates the binding
- `/ctx clear` removes the binding
- `/new` clears stored resume tokens for that topic

Topic names are derived from the context and follow the command style:
`project @branch` (no space after `@`). Renaming happens when the context changes.

State is stored in `telegram_topics_state.json` next to the config file.

### 6a. One shared chat with topics (`multi_project_chat`)

Use a single forum-enabled supergroup and create topics per project/branch.

```toml
[transports.telegram]
chat_id = -1001234567890

[transports.telegram.topics]
enabled = true
mode = "multi_project_chat"
```

Create a topic:

```
/topic z80 @main
```

No default project is assumed in this mode. Bind a topic (or use directives)
before running without explicit `/project` or `@branch`.

### 6b. One chat per project with topics (`per_project_chat`)

Each project gets its own forum-enabled supergroup. The project is inferred
from the chat.

```toml
[transports.telegram]
chat_id = 123456789 # main chat (must not match project chats)

[transports.telegram.topics]
enabled = true
mode = "per_project_chat"

[projects.z80]
path = "~/dev/z80"
chat_id = -1001111111111
```

Create a topic in the project chat:

```
/topic @main
```

In this mode, `/ctx set @branch` is enough because the project comes from the
chat.

## 7. Voice notes (optional)

Enable voice transcription:

```toml
[transports.telegram]
voice_transcription = true
```

Set `OPENAI_API_KEY` in the environment. If transcription fails, takopi replies
with a short error and skips the run.

## 8. Tips and common gotchas

- Bot needs **Manage Topics** permission for topic creation/renames.
- `watch_config = true` hot-reloads projects and engines (transport changes still
  require a restart).
- If a topic isn't bound, takopi will prompt you to use `/ctx set` or `/topic`.
- `--debug` writes `debug.log` in JSON format by default.
