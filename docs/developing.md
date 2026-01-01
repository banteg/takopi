# takopi - Developer Guide

This document describes the internal architecture and module responsibilities.
See `specification.md` for the authoritative behavior spec.

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

Takopi runs in **auto-router** mode by default. `default_engine` in `takopi.toml` selects
the engine for new threads; engine subcommands override that default for the process.

## Module Responsibilities

### `bridge.py` - Telegram bridge loop

The orchestrator module containing:

| Component | Purpose |
|-----------|---------|
| `BridgeConfig` | Frozen dataclass holding runtime config |
| `poll_updates()` | Async generator that drains backlog, long-polls updates, filters messages |
| `run_main_loop()` | TaskGroup-based main loop that spawns per-message handlers |
| `handle_message()` | Per-message handler with progress updates and final render |
| `ProgressEdits` | Throttled progress edit worker |
| `_handle_cancel()` | `/cancel` routing |

**Key patterns:**
- Bridge schedules runs FIFO per thread to avoid concurrent progress messages; runner locks enforce per-thread serialization
- `/cancel` routes by reply-to progress message id (accepts extra text)
- `/{engine}` on the first line selects the engine for new threads
- Progress edits are throttled to 2s intervals and only run when new events arrive
- Resume tokens are runner-formatted command lines (e.g., `` `codex resume <token>` ``)
- Resume parsing polls all runners via `AutoRouter.resolve_resume()` and routes to the first match
- Bot command menu is synced on startup (`cancel` + engine commands)

### `cli.py` - CLI entry point

| Component | Purpose |
|-----------|---------|
| `run()` / `main()` | Typer CLI entry points |
| `_parse_bridge_config()` | Reads config + builds `BridgeConfig` |

### `render.py` - Takopi event + Markdown helpers

| Function/Class | Purpose |
|----------------|---------|
| `render_markdown()` | Markdown → Telegram text + entities |
| `trim_body()` | Trim body to 3500 chars (header/footer preserved) |
| `prepare_telegram()` | Trim + render Markdown parts for Telegram |
| `ExecProgressRenderer` | Stateful renderer tracking recent actions for progress display |
| `render_event_cli()` | Format a takopi event for CLI logs |
| `format_elapsed()` | Formats seconds as `Xh Ym`, `Xm Ys`, or `Xs` |

### `telegram.py` - Telegram API wrapper

| Component | Purpose |
|-----------|---------|
| `BotClient` | Protocol defining the bot client interface |
| `TelegramClient` | HTTP client for Telegram Bot API (send, edit, delete messages) |

### `runners/codex.py` - Codex runner

| Component | Purpose |
|-----------|---------|
| `CodexRunner` | Spawns `codex exec --json`, streams JSONL, emits takopi events |
| `translate_codex_event()` | Normalizes Codex JSONL into the takopi event schema |
| `manage_subprocess()` | Starts a new process group and kills it on cancellation (POSIX) |

**Key patterns:**
- Per-resume locks (WeakValueDictionary) prevent concurrent resumes of the same session
- Event delivery uses a single internal queue to preserve order without per-event tasks
- Stderr is drained into a bounded tail (debug logging only)
- Event callbacks must not raise; callback errors abort the run

### `model.py` / `runner.py` - Core domain types

| File | Purpose |
|------|---------|
| `model.py` | Domain types: resume tokens, actions, events, run result |
| `runner.py` | Runner protocol + event queue utilities |

### `backends.py` - Engine backend contracts

Defines `EngineBackend`, `SetupIssue`, and the `EngineConfig` type used by
runner modules.

### `engines.py` - Engine backend discovery

Auto-discovers runner modules in `takopi.runners` that export `BACKEND`.

### `runners/` - Runner implementations

| File | Purpose |
|------|---------|
| `codex.py` | Codex runner (JSONL → takopi events) + per-resume locks |
| `mock.py` | Mock runner for tests/demos |

### `config.py` - Configuration loading

```python
def load_telegram_config() -> tuple[dict, Path]:
    # Loads ./.takopi/takopi.toml, then ~/.takopi/takopi.toml
```

### `logging.py` - Secure logging setup

```python
class RedactTokenFilter:
    # Redacts bot tokens from log output

def setup_logging(*, debug: bool):
    # Configures root logger with redaction filter
```

### `onboarding.py` - Setup validation

```python
def check_setup(backend: EngineBackend) -> SetupResult:
    # Validates engine CLI on PATH and config file

def render_setup_guide(result: SetupResult):
    # Displays rich panel with setup instructions
```

## Adding a Runner

See `docs/adding-a-runner.md` for the full guide and a worked example.

## Data Flow

### New Message Flow

```
Telegram Update
    ↓
poll_updates() drains backlog, long-polls, filters chat_id == from_id == cfg.chat_id
    ↓
run_main_loop() spawns tasks in TaskGroup
    ↓
router.resolve_resume(text, reply_text) → ResumeToken | None
    ↓
router.runner_for(resume_token) → selects runner (default engine if None)
    ↓
handle_message() spawned as task with selected runner
    ↓
Send initial progress message (silent)
    ↓
runner.run(prompt, resume_token)
    ├── Spawns engine subprocess (e.g., codex exec --json)
    ├── Streams JSONL from stdout
    ├── Normalizes JSONL -> takopi events
    ├── Yields Takopi events (async iterator)
    │       ↓
    │   ExecProgressRenderer.note_event()
    │       ↓
    │   ProgressEdits throttled edit_message_text()
    └── Ends with completed(resume, ok, answer)
    ↓
render_final() with resume line (runner-formatted)
    ↓
Send/edit final message
```

### Resume Flow

Same as above; auto-router polls all runners to extract resume tokens:
- Router returns first matching token (e.g. `` `claude --resume <id>` `` routes to Claude)
- Selected runner spawns with resume (e.g. `codex exec --json resume <token> -`)
- Per-token lock serializes concurrent resumes on the same thread

## Error Handling

| Scenario | Behavior |
|----------|----------|
| `codex exec` fails (rc != 0) | Emits a warning `action` plus `completed(ok=false, error=...)` |
| Telegram API error | Logged, edit skipped (progress continues) |
| Cancellation | Cancel scope terminates the process group (POSIX) and renders `cancelled` |
| Errors in handler | Final render uses `status=error` and preserves resume tokens when known |
| No agent_message (empty answer) | Final shows `error` status |
