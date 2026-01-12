# Takopi User Guide

Takopi is a command-line tool that lets you control coding agents—like Codex, Claude, and others—through Telegram. Send a message, and takopi runs the agent in your repo, streaming progress back to your chat. It supports multi-repo workflows, git worktrees, and per-project routing.

This guide starts simple and layers on features as you go. Jump to any section or read straight through.

## Prerequisites

Before you begin, make sure you have:

- A Telegram account
- Python 3.14+ and `uv` installed
- At least one supported agent CLI installed and on your `PATH` (codex, claude, opencode, pi)
- Basic familiarity with git (especially if you plan to use worktrees)

## Key concepts

A few terms you'll see throughout:

| Term | Meaning |
|------|---------|
| **Engine** | A coding agent backend (Codex, Claude, opencode, pi) |
| **Project** | A registered git repository with an alias |
| **Worktree** | A git feature that lets you check out multiple branches simultaneously in separate directories |
| **Topic** | A Telegram forum thread bound to a specific project/branch context |
| **Resume token** | State that allows an engine to continue from where it left off |

---

## 1. Installation and setup

Install takopi with:

```sh
uv tool install -U takopi
```

Run it once to start the onboarding wizard:

```sh
takopi
```

The wizard walks you through:

1. Creating a Telegram bot token via [@BotFather](https://t.me/BotFather)
2. Capturing your `chat_id` (the wizard listens for a message from you)
3. Choosing a default engine

To re-run onboarding later, use `takopi --onboard`.

Your configuration is stored at `~/.takopi/takopi.toml`.

### Minimal configuration

After onboarding, your config looks something like this:

```toml
default_engine = "codex"
transport = "telegram"

[transports.telegram]
bot_token = "123456789:ABCdefGHIjklMNOpqrsTUVwxyz"
chat_id = 123456789
```

Optional: split long final responses instead of trimming them:

```toml
[transports.telegram]
message_overflow = "split" # trim | split
```

---

## 2. Your first handoff

The simplest workflow:

1. `cd` into any git repository
2. Run `takopi`
3. Send a message to your bot

Takopi streams progress in the chat and sends a final response when the agent finishes.

### Basic controls

- **Reply** to a bot message with more instructions to continue the conversation
- **Cancel** a run by clicking the cancel button or replying to the progress message with `/cancel`

---

## 3. Switching engines

Prefix your message with an engine directive to override the default:

```
/codex hard reset the timeline
/claude shrink and store artifacts forever
/opencode hide their paper until they reply
/pi render a diorama of this timeline
```

Directives are only parsed at the start of the first non-empty line.

### Setting up engines

Takopi shells out to the agent CLIs. Install them and make sure they're on your `PATH`
(codex, claude, opencode, pi). Authentication is handled by each CLI (login,
config files, or environment variables).

---

## 4. Projects

For repos you work with often, register them as projects:

```sh
cd ~/dev/happy-gadgets
takopi init happy-gadgets
```

This adds a project entry to your config (for example):

```toml
[projects.happy-gadgets]
path = "~/dev/happy-gadgets"
```

Now you can target it from anywhere using the `/project` directive:

```
/happy-gadgets pinky-link two threads
```

If you expect to add or edit projects while takopi is running, enable config
watching so changes are picked up automatically:

```toml
watch_config = true
```

### Project-specific settings

Projects can override global defaults:

```toml
[projects.happy-gadgets]
path = "~/dev/happy-gadgets"
default_engine = "claude"
worktrees_dir = ".worktrees"
worktree_base = "master"
```

### Setting a default project

If you mostly work in one repo:

```toml
default_project = "happy-gadgets"
```

---

## 5. Worktrees

Worktrees let you work on multiple branches without switching back and forth. Use `@branch` to run a task in a dedicated worktree:

```
/happy-gadgets @feat/memory-box freeze artifacts forever
```

Takopi creates (or reuses) a worktree at:

```
<worktrees_root>/<branch>
```

`worktrees_root` is `<project.path>/<worktrees_dir>` unless `worktrees_dir` is an
absolute path. If the branch matches the repo's current branch, Takopi runs in the
main repo instead of creating a new worktree.

### Worktree configuration

```toml
[projects.happy-gadgets]
path = "~/dev/happy-gadgets"
worktrees_dir = ".worktrees"      # relative to project path
worktree_base = "master"          # base branch for new worktrees
```

To avoid `.worktrees/` showing up as untracked, add it to your global gitignore:

```sh
git config --global core.excludesfile ~/.config/git/ignore
echo ".worktrees/" >> ~/.config/git/ignore
```

### Context persistence

Takopi adds a `ctx:` footer to messages with project and branch info. When you reply, this context carries forward—no need to repeat `/project @branch` each time.

---

## 6. Per-project chat routing

Give each project its own Telegram chat:

```sh
takopi chat-id --project happy-gadgets
```

Send any message in the target chat. Takopi captures the `chat_id` and updates your config:

```toml
[projects.happy-gadgets]
path = "~/dev/happy-gadgets"
chat_id = -1001234567890
```

Messages from that chat automatically route to the project.

### Rules for chat IDs

- Each `projects.*.chat_id` must be unique
- Project chat IDs must not match `transports.telegram.chat_id`
- Telegram uses positive IDs for private chats and negative IDs for groups/supergroups

### Capture a chat ID without saving

To see a chat ID without writing to config:

```sh
takopi chat-id
```

---

## 7. Topics

Topics bind Telegram forum threads to specific project/branch contexts. They also preserve resume tokens, so agents can pick up where they left off.

### Enabling topics

```toml
[transports.telegram.topics]
enabled = true
```

Your bot needs **Manage Topics** permission in the group.

If any `projects.<alias>.chat_id` are configured, topics are managed in those
project chats; otherwise topics are managed in the main chat.

### Topic behavior

```
┌────────────────────────────┐
│ takopi projects            │
├────────────────────────────┤
│ takopi @master             │
│ takopi @feat/topics        │
│ happy-gadgets @master      │
│ happy-gadgets @feat/camera │
└────────────────────────────┘
```

Each project can have its own forum-enabled supergroup. Topics still
include the project name for consistency, but the project is inferred from the
chat. Regular messages in that chat also infer the project, so `/project` is
usually optional.

```
┌────────────────────────────────┐  ┌───────────────────────────────────┐
│ takopi                         │  │ happy-gadgets                     │
├────────────────────────────────┤  ├───────────────────────────────────┤
│ takopi @master                 │  │ happy-gadgets @master             │
│ takopi @feat/topics            │  │ happy-gadgets @feat/happy-camera  │
│ takopi @feat/voice             │  │ happy-gadgets @feat/memory-box    │
└────────────────────────────────┘  └───────────────────────────────────┘
```

### Topic commands

Run these inside a topic thread:

| Command | Description |
|---------|-------------|
| `/topic <project> @branch` | Create a new topic bound to context |
| `/ctx` | Show the current binding |
| `/ctx set <project> @branch` | Update the binding |
| `/ctx clear` | Remove the binding |
| `/new` | Clear resume tokens for this topic |

In project chats, omit the project: `/topic @branch` or `/ctx set @branch`.

### Configuration examples

**Main chat only:**

```toml
[transports.telegram]
chat_id = -1001234567890

[transports.telegram.topics]
enabled = true
```

**Project chats:**

```toml
[transports.telegram]
chat_id = 123456789   # main chat (private, for non-project messages)

[transports.telegram.topics]
enabled = true

[projects.takopi]
path = "~/dev/takopi"
chat_id = -1001111111111   # forum-enabled group
```

Topic state is stored in `telegram_topics_state.json` next to your config file.

---

## 8. Voice notes

Dictate tasks instead of typing:

```toml
[transports.telegram]
voice_transcription = true
voice_transcription_model = "gpt-4o-mini-transcribe" # optional
```

Set `OPENAI_API_KEY` in your environment (uses OpenAI's transcription API with the
`gpt-4o-mini-transcribe` model by default). To use a local OpenAI-compatible
Whisper server, also set `OPENAI_BASE_URL` (for example,
`http://localhost:8000/v1`) and a dummy `OPENAI_API_KEY` if your server ignores it.
If your server requires a specific model name, set `voice_transcription_model`
accordingly (for example, `whisper-1`).

When you send a voice note, takopi transcribes it and runs the result as a normal text message. If transcription fails, you'll get an error message and the run is skipped.

---

---

## 9. File transfer

Upload files into the active repo/worktree or fetch files back into Telegram.

### Upload a file

Send a document with a caption:

```
/file put <path>
```

Examples:

```
/file put docs/spec.pdf
/file put /happy-gadgets @feat/camera assets/logo.png
```

If you send a file **without a caption**, takopi saves it to:

```
incoming/<original_filename>
```

Use `--force` to overwrite an existing file:

```
/file put --force docs/spec.pdf
```

### Fetch a file

Send:

```
/file get <path>
```

Directories are zipped automatically.

### File transfer config

```toml
[transports.telegram.files]
enabled = true
auto_put = true
uploads_dir = "incoming"
allowed_user_ids = [123456789]
deny_globs = [".git/**", ".env", ".envrc", "**/*.pem", "**/.ssh/**"]
```

Notes:
- File transfer is **disabled by default**.
- If `allowed_user_ids` is empty, private chats are allowed and group usage requires admin privileges.

---

## 10. Configuration reference

Full example with all options:

```toml
# Global defaults
default_engine = "codex"
default_project = "takopi"
transport = "telegram"
watch_config = true   # hot-reload on config changes (except transport)

[transports.telegram]
bot_token = "123456789:ABCdefGHIjklMNOpqrsTUVwxyz"
chat_id = 123456789
voice_transcription = true
# voice_transcription_model = "gpt-4o-mini-transcribe"

[transports.telegram.files]
enabled = true
auto_put = true
uploads_dir = "incoming"
allowed_user_ids = [123456789]
deny_globs = [".git/**", ".env", ".envrc", "**/*.pem", "**/.ssh/**"]

[transports.telegram.topics]
enabled = true
scope = "auto"

# Project definitions
[projects.takopi]
path = "~/dev/takopi"
default_engine = "codex"
worktrees_dir = ".worktrees"
worktree_base = "master"
# chat_id = -1001234567890   # optional: dedicated chat

[projects.happy-planet]
path = "~/dev/happy-planet"
default_engine = "claude"
worktrees_dir = "~/.takopi/worktrees/happy-planet"
worktree_base = "develop"
```

---

## 11. Command cheatsheet

### Message directives

| Directive | Example | Description |
|-----------|---------|-------------|
| `/engine` | `/codex make threads resolve their differences` | Use a specific engine |
| `/project` | `/happy-gadgets add escape-pod` | Target a project |
| `@branch` | `@feat/happy-camera rewind to checkpoint` | Run in a worktree |
| Combined | `/happy-gadgets @feat/flower-pin observe unseen` | Project + branch |

### In-chat commands

| Command | Description |
|---------|-------------|
| `/cancel` | Reply to the progress message to stop the current run |
| `/file put <path>` | Upload a document into the repo/worktree |
| `/file get <path>` | Fetch a file (directories are zipped) |
| `/topic <project> @branch` | Create/bind a topic |
| `/ctx` | Show current context |
| `/ctx set <project> @branch` | Update context binding |
| `/ctx clear` | Remove context binding |
| `/new` | Clear resume tokens |

### CLI commands

| Command | Description |
|---------|-------------|
| `takopi` | Start the bot (runs onboarding if first time) |
| `takopi --onboard` | Re-run onboarding wizard |
| `takopi init <alias>` | Register current directory as a project |
| `takopi chat-id` | Capture a chat ID |
| `takopi chat-id --project <alias>` | Set a project's chat ID |
| `takopi --debug` | Write debug logs to `debug.log` |

---

## 12. Tips

### Schedule tasks with Telegram

Telegram has a native message scheduling feature that works seamlessly with takopi. Long-press the send button and choose "Schedule Message" to run tasks at a specific time. You can also set up recurring schedules (daily, weekly, etc.) for automated workflows.

---

## 13. Troubleshooting

If something isn't working, rerun with `takopi --debug` and check `debug.log`
for errors. Include it when reporting issues.
