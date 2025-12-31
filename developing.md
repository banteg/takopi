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

## Module Responsibilities

### `bridge.py` - Telegram bridge loop

The orchestrator module containing:

| Component | Purpose |
|-----------|---------|
| `BridgeConfig` | Frozen dataclass holding runtime config |
| `poll_updates()` | Async generator that drains backlog, long-polls updates, filters messages |
| `_run_main_loop()` | TaskGroup-based main loop that spawns per-message handlers |
| `handle_message()` | Per-message handler with progress updates and final render |
| `ProgressEdits` | Throttled progress edit worker |
| `_handle_cancel()` | `/cancel` routing |
| `truncate_for_telegram()` | Moved to `markdown.py` |

**Key patterns:**
- Worker pool with an AnyIO memory stream limits concurrency (default: 16 workers)
- `/cancel` routes by reply-to progress message id (accepts extra text)
- Progress edits are throttled to ~1s intervals and only run when new events arrive
- Resume tokens are runner-formatted command lines (e.g., `` `codex resume <token>` ``)
- Resume parsing is delegated to the active runner (no cross-engine fallback)

### `cli.py` - CLI entry point

| Component | Purpose |
|-----------|---------|
| `run()` / `main()` | Typer CLI entry points |
| `_parse_bridge_config()` | Reads config + builds `BridgeConfig` |

### `markdown.py` - Telegram markdown helpers

| Function | Purpose |
|----------|---------|
| `render_markdown()` | Markdown → Telegram text + entities |
| `prepare_telegram()` | Render + truncate for Telegram limits |
| `truncate_for_telegram()` | Smart truncation preserving resume lines |

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

### `render.py` - Takopi event rendering

Transforms takopi events into human-readable text:

| Function/Class | Purpose |
|----------------|---------|
| `ExecProgressRenderer` | Stateful renderer tracking recent actions for progress display |
| `render_event_cli()` | Format a takopi event for CLI logs |
| `format_elapsed()` | Formats seconds as `Xh Ym`, `Xm Ys`, or `Xs` |
| `render_markdown()` | Moved to `markdown.py` |

**Supported event types:**
- `session.started`
- `action.started`, `action.completed`
- `log`, `error`

### `model.py` / `runner.py` - Core domain types

| File | Purpose |
|------|---------|
| `model.py` | Domain types: resume tokens, actions, events, run result |
| `runner.py` | Runner protocol + event queue utilities |

### `runners/` - Runner implementations

| File | Purpose |
|------|---------|
| `engines.py` | Engine backend registry (setup checks + runner construction) |
| `runners/base.py` | Re-export shim for legacy imports |
| `runners/codex.py` | Codex runner (JSONL → takopi events) + per-resume locks |
| `runners/mock.py` | Mock runner for tests/demos |

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

1. Implement the `Runner` protocol in `src/takopi/runners/<engine>.py`.
2. Emit Takopi events from `takopi.model` and implement resume helpers
   (`format_resume`, `extract_resume`, `is_resume_line`).
3. Register an `EngineBackend` in `src/takopi/engines.py` with setup checks
   and runner construction.
4. Extend tests (runner contract + any engine-specific translation tests).

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
    └── Returns RunResult(resume, answer)
    ↓
render_final() with resume line (runner-formatted)
    ↓
Send/edit final message
```

### Resume Flow

Same as above, but:
- Runners parse resume lines (e.g. `` `codex resume <token>` ``)
- Command becomes: `codex exec --json resume <token> -`
- Per-token lock serializes concurrent resumes

## Error Handling

| Scenario | Behavior |
|----------|----------|
| `codex exec` fails (rc != 0) | Emits an `error` event and raises `RuntimeError` with stderr tail |
| Telegram API error | Logged, edit skipped (progress continues) |
| Cancellation | Cancel scope terminates the process group (POSIX) and renders `cancelled` |
| Errors in handler | Final render uses `status=error` and preserves resume tokens when known |
| No agent_message (empty answer) | Final shows `error` status |
