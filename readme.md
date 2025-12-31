# takopi

🐙 *A little helper from Happy Planet, here to make your Codex sessions happier-pi!*

A Telegram bot that bridges messages to [Codex](https://github.com/openai/codex) sessions using non-interactive `codex exec --json`.

## Features

- **Stateless Resume**: No database required—sessions are resumed via `` `codex resume <token>` `` lines embedded in messages
- **Progress Updates**: Real-time progress edits showing commands, tools, and elapsed time
- **Markdown Rendering**: Full Telegram-compatible markdown with entity support
- **Concurrency**: Parallel runs across threads with per-session serialization

## Quick Start

### Prerequisites

- [uv](https://github.com/astral-sh/uv) package manager
- Python 3.14+
- Codex CLI on PATH

### Installation

```bash
# Install with uv, then run as `takopi`
uv tool install takopi
takopi

# or run with uvx
uvx takopi
```

### Setup

1. **Start the bot**: Send `/start` to your bot in Telegram—it can't message you until you do
2. **Trust your working directory**: Run `codex` once interactively in your project directory (must be a git repo) to add it to trusted directories

### Configuration

Create `~/.takopi/takopi.toml` (or `.takopi/takopi.toml` for a repo-local config):

```toml
bot_token = "123456789:ABCdefGHIjklMNOpqrsTUVwxyz"
chat_id = 123456789

[codex]
# Optional: Codex profile name (defined in ~/.codex/config.toml)
profile = "takopi"
# Optional: extra args passed before `codex exec`
extra_args = ["-c", "notify=[]"]
```

Engine-specific settings live under a table named after the engine id (e.g. `[codex]`).

| Key | Description |
|-----|-------------|
| `bot_token` | Telegram Bot API token from [@BotFather](https://t.me/BotFather) |
| `chat_id` | Your Telegram user ID from [@myidbot](https://t.me/myidbot) |

The bridge only accepts messages where the chat ID equals the sender ID and both match `chat_id` (i.e., private chat with that user).

### Codex Profile (Optional)

Create a Codex profile in `~/.codex/config.toml`:

```toml
[profiles.takopi]
model = "gpt-5.2-codex"
```

Then set `profile = "takopi"` under `[codex]` in `~/.takopi/takopi.toml`.

### Options

| Flag | Default | Description |
|------|---------|-------------|
| `--final-notify` / `--no-final-notify` | `--final-notify` | Send final response as new message (vs. edit) |
| `--debug` / `--no-debug` | `--no-debug` | Enable verbose logging |
| `--engine ID` | `codex` | Engine backend id |
| `--engine-option KEY=VALUE` |  | Engine-specific override (repeatable) |
| `--version` |  | Show the version and exit |

## Usage

### New Conversation

Send any message to your bot. The bridge will:

1. Send a silent progress message
2. Stream events from `codex exec`
3. Update progress every ~1 second
4. Send final response with a resume token line

### Resume a Session

Reply to a bot message (containing `` `codex resume <token>` ``), or include the resume line in your message:

```
`codex resume 019b66fc-64c2-7a71-81cd-081c504cfeb2`
```

### Cancel a Run

Reply to a progress message with `/cancel` to stop the running execution.

## Notes

- **Startup**: Pending updates are drained (ignored) on startup
- **Progress**: Updates are throttled to ~1s intervals, sent silently
- **Queueing**: Messages for the same thread queue behind the active run without consuming extra concurrency slots
- **Filtering**: Only accepts messages where chat ID equals sender ID and matches `chat_id`
- **Single instance**: Run exactly one instance per bot token—multiple instances will race for updates

## Development

See [`developing.md`](developing.md) and `specification.md` for architecture and behavior details.

## License

MIT
