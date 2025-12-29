# Takopi

> 🐙 A little helper from Happy Planet, here to make your Codex sessions happier-pi!

A Telegram bot that bridges messages to [Codex](https://github.com/openai/codex) sessions using non-interactive `codex exec` and `codex exec resume`.

## Features

- **Stateless Resume**: No database required—sessions are resumed via `resume: <uuid>` lines embedded in messages
- **Progress Updates**: Real-time progress edits showing commands, tools, and elapsed time
- **Markdown Rendering**: Full Telegram-compatible markdown with entity support
- **Concurrency**: Handles multiple conversations with per-session serialization
- **Token Redaction**: Automatically redacts Telegram tokens from logs

## Quick Start

### Prerequisites

- Python 3.12+
- [uv](https://github.com/astral-sh/uv) package manager
- Codex CLI on PATH

### Installation

```bash
# Clone and enter the directory
cd takopi

# Run directly with uv (installs deps automatically)
uv run takopi --help
```

### Configuration

Create `~/.codex/takopi.toml` (or `./.codex/takopi.toml` for a repo-local config):

```toml
bot_token = "123456789:ABCdefGHIjklMNOpqrsTUVwxyz"
chat_id = 123456789
```

| Key | Description |
|-----|-------------|
| `bot_token` | Telegram Bot API token from [@BotFather](https://t.me/BotFather) |
| `chat_id` | Allowed chat ID (also used for startup notifications) |

The bridge only accepts messages where the chat ID equals the sender ID and both match `chat_id` (i.e., private chat with that user).

When you pass `--cd`, Takopi looks for `.codex/takopi.toml` under that directory first.

### Codex Profile (Optional)

Create a Codex profile in `~/.codex/config.toml`:

```toml
[profiles.takopi]
model = "gpt-4.1"
```

Then run Takopi with:

```bash
uv run takopi --profile takopi
```

### Running

```bash
uv run takopi
```

#### Options

| Flag | Default | Description |
|------|---------|-------------|
| `--final-notify` / `--no-final-notify` | `--final-notify` | Send final response as new message (vs. edit) |
| `--debug` / `--no-debug` | `--no-debug` | Enable verbose logging |
| `--cd PATH` | cwd | Working directory for Codex |
| `--profile NAME` | (codex default) | Codex profile name |

## Usage

### New Conversation

Send any message to your bot. The bridge will:

1. Send a silent progress message
2. Stream events from `codex exec`
3. Update progress every ~2 seconds
4. Send final response with session ID

### Resume a Session

Reply to a bot message (containing `resume: <uuid>`), or include the resume line in your message:

```
resume: `019b66fc-64c2-7a71-81cd-081c504cfeb2`
```

## Behavior Notes

- **Startup**: Pending updates are drained (ignored) on startup
- **Progress**: Updates are throttled to ~2s intervals, sent silently
- **Notifications**: Codex's built-in notify is disabled (bridge handles it)
- **Filtering**: Only accepts messages where chat ID equals sender ID and matches `chat_id`

## Operational Notes

### Single consumer: do not run multiple instances per bot token

Run exactly one instance per bot token.

This bridge uses Telegram's `getUpdates` long-polling. Telegram stores incoming updates in a
single queue per bot and considers an update confirmed once a client calls `getUpdates` with an
`offset` higher than the update's `update_id` (see the [Telegram Bot API](https://core.telegram.org/bots/api)).
Running two bridge processes with the same bot token will cause them to compete for updates
(duplicates and/or missed messages).

## Development

See [`developing.md`](developing.md) for architecture details.

```bash
# Run tests
uv run pytest

# Run with debug logging
uv run takopi --debug 2>&1 | tee debug.log
```

## License

MIT
