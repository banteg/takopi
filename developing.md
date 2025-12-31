# takopi - Developer Guide

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

### `exec_bridge.py` - Telegram bridge loop

The orchestrator module containing:

| Component | Purpose |
|-----------|---------|
| `main()` / `run()` | CLI entry point via Typer |
| `BridgeConfig` | Frozen dataclass holding runtime config |
| `poll_updates()` | Async generator that drains backlog, long-polls updates, filters messages |
| `_run_main_loop()` | TaskGroup-based main loop that spawns per-message handlers |
| `handle_message()` | Per-message handler with progress updates and final render |
| `ProgressEdits` | Throttled progress edit worker |
| `RunnerRouter` | Selects a runner and delegates resume parsing to runners |
| `truncate_for_telegram()` | Smart truncation preserving resume lines |

**Key patterns:**
- Worker pool with an AnyIO memory stream limits concurrency (default: 16 workers)
- `/cancel` maps progress message ids to an AnyIO CancelScope for immediate cancellation
- Progress edits are throttled to ~1s intervals and only run when new events arrive
- Resume tokens are engine-qualified for reliable routing
- Runner routing prefers the resume token engine and falls back to the default runner

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

### `exec_render.py` - Takopi event rendering

Transforms takopi events into human-readable text:

| Function/Class | Purpose |
|----------------|---------|
| `ExecProgressRenderer` | Stateful renderer tracking recent actions for progress display |
| `render_event_cli()` | Format a takopi event for CLI logs |
| `format_elapsed()` | Formats seconds as `Xh Ym`, `Xm Ys`, or `Xs` |
| `render_markdown()` | Markdown to Telegram text + entities (markdown-it-py + sulguk) |

**Supported event types:**
- `session.started`
- `action.started`, `action.completed`
- `log`, `error`

### `runners/` - Runner protocol & engines

| File | Purpose |
|------|---------|
| `runners/base.py` | Runner protocol + takopi event types |
| `runners/codex.py` | Codex runner (JSONL to takopi events) + per-resume locks |
| `runners/mock.py` | Mock runner for tests/demos |

### `config.py` - Configuration loading

```python
def load_telegram_config() -> tuple[dict, Path]:
    # Loads ./.codex/takopi.toml, then ~/.codex/takopi.toml
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
    ├── Normalizes JSONL -> takopi events
    ├── Pushes events into ordered sink queue
    │       ↓
    │   ExecProgressRenderer.note_event()
    │       ↓
    │   ProgressEdits throttled edit_message_text()
    └── Returns RunResult(resume, answer, ok)
    ↓
render_final() with resume line (engine-qualified)
    ↓
Send/edit final message
```

### Resume Flow

Same as above, but:
- Runners parse resume lines (e.g. `` `codex resume <token>` ``)
- Codex runner accepts legacy ``resume: `<uuid>` `` and ``resume: `codex:<uuid>` `` for compatibility
- Command becomes: `codex exec --json resume <token> -`
- Per-token lock serializes concurrent resumes

## Error Handling

| Scenario | Behavior |
|----------|----------|
| `codex exec` fails (rc != 0) before any agent message | Raises `RuntimeError` with stderr tail |
| `codex exec` fails (rc != 0) after agent message | Emits a `log` event and returns last answer |
| Telegram API error | Logged, edit skipped (progress continues) |
| Cancellation | Cancel scope terminates the process group (POSIX) and renders `cancelled` |
| Errors in handler | Final render uses `status=error` and preserves resume tokens when known |
| No agent_message | Final shows `error` status |
