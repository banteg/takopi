# takopi — Developer Guide

This document describes the internal architecture and module responsibilities.

## Development Setup

```bash
# Clone and enter the directory
git clone https://github.com/banteg/takopi
cd takopi

# Run directly with uv (installs deps automatically)
uv run takopi --help

# Or install locally from the repo to test outside the repo
uv tool install .
takopi --help

# Run tests, linting, type checking
uv run pytest
uv run ruff check src tests
uv run ty check .

# Or all at once
make check
```

## Module Responsibilities

### `exec_bridge.py` — Main Entry Point

The orchestrator module containing:

| Component | Purpose |
|-----------|---------|
| `main()` / `run()` | CLI entry point via Typer |
| `BridgeConfig` | Frozen dataclass holding runtime config |
| `CodexRunner` | Spawns `codex exec`, streams JSONL, emits takopi events |
| `poll_updates()` | Async generator that drains backlog, long-polls updates, filters messages |
| `_run_main_loop()` | TaskGroup-based main loop that spawns per-message handlers |
| `handle_message()` | Per-message handler with progress updates |
| `extract_resume_token()` | Parses `resume: \`<engine>:<token>\`` from message text |
| `truncate_for_telegram()` | Smart truncation preserving resume lines |

**Key patterns:**
- Per-session locks prevent concurrent resumes to the same `session_id`
- Worker pool with an AnyIO memory stream limits concurrency (default: 16 workers)
- AnyIO task groups manage worker tasks
- Progress edits are throttled to ~2s intervals
- Subprocess stderr is drained to a bounded deque for error reporting
- `poll_updates()` uses Telegram `getUpdates` long-polling with a single server-side updates
  queue per bot token; updates are confirmed when a client requests a higher `offset`, so
  multiple instances with the same token will race (duplicates and/or missed updates)

### `telegram.py` — Telegram Bot API

Minimal async client wrapping the Bot API:

```python
class TelegramClient:
    async def get_updates(...)   # Long-polling
    async def send_message(...)  # With entities support
    async def edit_message_text(...)
    async def delete_message(...)
```

**Features:**
- Automatic retry on 429 (rate limit) with `retry_after`
- Raises `TelegramAPIError` with payload details on failure

### `exec_render.py` — Takopi Event Rendering

Transforms takopi events into human-readable text:

| Function/Class | Purpose |
|----------------|---------|
| `format_event()` | Core dispatcher returning `(item_num, cli_lines, progress_line, prefix)` |
| `render_event_cli()` | Simplified wrapper for console logging |
| `ExecProgressRenderer` | Stateful renderer tracking recent actions for progress display |
| `format_elapsed()` | Formats seconds as `Xh Ym`, `Xm Ys`, or `Xs` |
| `render_markdown()` | Markdown → Telegram text + entities (markdown-it-py + sulguk) |

**Supported event types:**
- `session.started`
- `action.started`, `action.completed`
- `log`, `error`

### `runners/` — Runner Protocol & Engines

| File | Purpose |
|------|---------|
| `runners/base.py` | Runner protocol + takopi event types |
| `runners/codex.py` | Codex runner (JSONL → takopi events) + per-resume locks |
| `runners/mock.py` | Mock runner for tests/demos |

### `config.py` — Configuration Loading

```python
def load_telegram_config() -> tuple[dict, Path]:
    # Loads ./.codex/takopi.toml, then ~/.codex/takopi.toml
```

### `logging.py` — Secure Logging Setup

```python
class RedactTokenFilter:
    # Redacts bot tokens from log output

def setup_logging(*, debug: bool):
    # Configures root logger with redaction filter
```

### `onboarding.py` — Setup Validation

```python
def check_setup() -> SetupResult:
    # Validates codex CLI on PATH and config file

def render_setup_guide(result: SetupResult):
    # Displays rich panel with setup instructions
```

## Data Flow

### New Message Flow

```
Telegram Update
    ↓
poll_updates() drains backlog, long-polls, filters chat_id == from_id == cfg.chat_id
    ↓
_run_main_loop() spawns tasks in TaskGroup
    ↓
handle_message() spawned as task
    ↓
Send initial progress message (silent)
    ↓
CodexRunner.run()
    ├── Spawns: codex exec --json ... -
    ├── Streams JSONL from stdout
    ├── Normalizes JSONL → takopi events
    ├── Calls on_event() for each event
    │       ↓
    │   ExecProgressRenderer.note_event()
    │       ↓
    │   Throttled edit_message_text()
    └── Returns (resume_token, answer, saw_agent_message)
    ↓
render_final() with resume line (engine-qualified)
    ↓
Send/edit final message
```

### Resume Flow

Same as above, but:
- `extract_resume_token()` finds the last `resume: \`<engine>:<token>\`` line in message or reply
- Runner interprets legacy UUIDs vs engine-qualified tokens
- Command becomes: `codex exec --json resume <token> -`
- Per-token lock serializes concurrent resumes

## Error Handling

| Scenario | Behavior |
|----------|----------|
| `codex exec` fails (rc≠0) before any agent message | Raises `RuntimeError` with stderr |
| `codex exec` fails (rc≠0) after agent message | Emits a `log` event and returns last answer |
| Telegram API error | Logged, edit skipped (progress continues) |
| Cancellation | Cancel scope triggers terminate; cancellation is detected via `cancelled_caught` |
| No agent_message | Final shows "error" status |
