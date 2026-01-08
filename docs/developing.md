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
just check
```

Takopi runs in **auto-router** mode by default. `default_engine` in `takopi.toml` selects
the engine for new threads; engine subcommands override that default for the process.

## Module Responsibilities

### `runner_bridge.py` - Transport-agnostic orchestration

The core handler module containing:

| Component | Purpose |
|-----------|---------|
| `ExecBridgeConfig` | Frozen dataclass holding transport + presenter config |
| `IncomingMessage` | Normalized incoming message shape |
| `handle_message()` | Per-message handler with progress updates and final render |
| `ProgressEdits` | Throttled progress edit worker |
| `RunningTask` | Cancellation + resume coordination for in-flight runs |

**Key patterns:**
- Progress edits are best-effort and only run when new events arrive (Telegram outbox handles rate limiting/coalescing)
- Resume tokens are runner-formatted command lines (e.g., `` `codex resume <token>` ``, `` `claude --resume <token>` ``, `` `pi --session <path>` ``)
- Resume lines are stripped from the prompt before invoking the runner
- Errors/cancellation render final status while preserving resume tokens when known

### `telegram/bridge.py` - Telegram bridge loop

The Telegram adapter module containing:

| Component | Purpose |
|-----------|---------|
| `TelegramBridgeConfig` | Frozen dataclass holding bot + router + exec config |
| `TelegramTransport` | `BotClient` → `Transport` adapter |
| `TelegramPresenter` | `ProgressState` → `RenderedMessage` adapter |
| `poll_updates()` | Async generator that drains backlog, long-polls updates, filters messages |
| `run_main_loop()` | TaskGroup-based main loop that spawns per-message handlers |
| `_handle_cancel()` | `/cancel` routing |

**Key patterns:**
- Bridge schedules runs FIFO per thread to avoid concurrent progress messages; runner locks enforce per-thread serialization
- `/cancel` routes by reply-to progress message id (accepts extra text)
- `/{engine}` on the first line selects the engine for new threads
- Resume parsing polls all runners via `AutoRouter.resolve_resume()` and routes to the first match
- Bot command menu is synced on startup (`cancel` + engine + project commands, capped at 100)

### `transport.py` - Transport protocol

Defines `Transport`, `MessageRef`, `RenderedMessage`, and `SendOptions`.

### `presenter.py` - Presenter protocol

Defines a renderer that converts `ProgressState` into `RenderedMessage` outputs.

### `transports.py` - Transport registry

Defines the transport backend protocol, registry helpers, and built-in transport registration.

### `config_migrations.py` - Config migrations

Applies one-time edits to on-disk config (e.g., legacy Telegram key migration) before
`TakopiSettings` validation runs.

### `telegram/backend.py` - Telegram transport backend

Adapter that validates Telegram config, runs onboarding, and builds/runs the Telegram bridge.

### `cli.py` - CLI entry point

| Component | Purpose |
|-----------|---------|
| `run()` / `main()` | Typer CLI entry points |
| `_run_auto_router()` | Loads settings, resolves transport + engine, builds router, delegates to transport backend |

### `progress.py` - Progress tracking

| Function/Class | Purpose |
|----------------|---------|
| `ProgressTracker` | Stateful reducer of takopi events into progress snapshots |
| `ProgressState` | Snapshot of actions, resume token, and engine metadata |

### `markdown.py` - Markdown formatting

| Function/Class | Purpose |
|----------------|---------|
| `MarkdownFormatter` | Converts `ProgressState` into MarkdownParts |
| `MarkdownPresenter` | `ProgressState` → `RenderedMessage` (markdown text) |
| `MarkdownParts` | Header/body/footer building blocks for markdown output |
| `assemble_markdown_parts()` | Join MarkdownParts into a single markdown string |
| `render_event_cli()` | Format a takopi event for CLI logs |
| `format_elapsed()` | Formats seconds as `Xh Ym`, `Xm Ys`, or `Xs` |

### `telegram/render.py` - Telegram markdown rendering

| Function/Class | Purpose |
|----------------|---------|
| `render_markdown()` | Markdown → Telegram text + entities |
| `trim_body()` | Trim body to 3500 chars (header/footer preserved) |
| `prepare_telegram()` | Trim + render Markdown parts for Telegram |

### `telegram/client.py` - Telegram API wrapper

| Component | Purpose |
|-----------|---------|
| `BotClient` | Protocol defining the bot client interface |
| `TelegramClient` | HTTP client for Telegram Bot API (send, edit, delete messages) |

See `docs/transports/telegram.md` for outbox behavior, rate limiting, and retry rules.

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
- Translation errors abort the run; keep event normalization defensive

### `runners/pi.py` - Pi runner

| Component | Purpose |
|-----------|---------|
| `PiRunner` | Spawns `pi --print --mode json`, streams JSONL, emits takopi events |
| `translate_pi_event()` | Normalizes Pi JSONL into the takopi event schema |

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
| `claude.py` | Claude runner (JSONL → takopi events) + per-resume locks |
| `opencode.py` | OpenCode runner (JSONL → takopi events) + per-resume locks |
| `pi.py` | Pi runner (JSONL → takopi events) + per-resume locks |
| `mock.py` | Mock runner for tests/demos |

### `schemas/` - JSONL decoding schemas

Self-documenting msgspec schemas for decoding engine JSONL streams.

| File | Purpose |
|------|---------|
| `codex.py` | `codex exec --json` event schemas |
| `claude.py` | `claude -p --output-format stream-json --verbose` event schemas |
| `opencode.py` | `opencode run --format json` event schemas |
| `pi.py` | `pi --print --mode json` event schemas |

### `utils/` - Utility modules

| File | Purpose |
|------|---------|
| `paths.py` | `relativize_path()`, `relativize_command()` helpers |
| `streams.py` | `iter_bytes_lines()`, `drain_stderr()` for async stream handling |
| `subprocess.py` | `manage_subprocess()`, `terminate_process()`, `kill_process()` |

### `router.py` - Auto-router

| Component | Purpose |
|-----------|---------|
| `AutoRouter` | Resolves resume tokens by polling all runners, routes to matching engine |
| `RunnerEntry` | Dataclass holding runner + backend metadata |
| `RunnerUnavailableError` | Raised when requested engine is not available |

### `scheduler.py` - Thread scheduling

| Component | Purpose |
|-----------|---------|
| `ThreadScheduler` | Per-thread FIFO job queuing with serialization |
| `ThreadJob` | Dataclass representing a queued job |
| `note_thread_known()` | Registers a thread as busy when token discovered mid-run |

### `events.py` - Event factory

| Component | Purpose |
|-----------|---------|
| `EventFactory` | Helper class for creating takopi events with consistent engine/resume |
| Builder methods | `started()`, `action()`, `action_started()`, `action_updated()`, `action_completed()`, `completed()`, `completed_ok()`, `completed_error()` |

### `lockfile.py` - Single-instance enforcement

| Component | Purpose |
|-----------|---------|
| `acquire_lock()` | Acquire lock for bot token, returns `LockHandle` context manager |
| `LockHandle` | Context manager for automatic lock release |
| `LockInfo` | Dataclass with `pid` and `token_fingerprint` |
| `token_fingerprint()` | SHA256 hash of bot token, truncated to 10 chars |

### `backends_helpers.py` - Backend utilities

| Function | Purpose |
|----------|---------|
| `install_issue()` | Creates `SetupIssue` with install instructions for missing CLI |

### `config.py` - Shared configuration errors

```python
class ConfigError(RuntimeError): ...
```

### `settings.py` - Settings loading

```python
def load_settings(path: str | Path | None = None) -> tuple[TakopiSettings, Path]:
    # Loads ~/.takopi/takopi.toml (TOML + env), validates via pydantic-settings
```

### `config_store.py` - Raw TOML read/write

```python
def read_raw_toml(path: Path) -> dict:
    # Loads TOML for merge/update without clobbering extra sections
```

### `logging.py` - Secure logging setup

```python
def setup_logging(*, debug: bool = False) -> None:
    # Configures structlog pipeline, redaction, and output formatting.
```

Environment flags:

- `TAKOPI_LOG_LEVEL` (default `info`, `debug` forces `debug`)
- `TAKOPI_LOG_FORMAT` (`console` or `json`)
- `TAKOPI_LOG_COLOR` (`1/true/yes/on` to force color, `0/false/no/off` to disable)
- `TAKOPI_LOG_FILE` (append JSON lines to a file)
- `TAKOPI_TRACE_PIPELINE` (log pipeline events at info instead of debug)
- `TAKOPI_NO_INTERACTIVE` (disable interactive prompts for CI/non-TTY environments)
- `PI_CODING_AGENT_DIR` (override Pi agent session directory base path)

CLI flag: `--debug` enables debug logging (overrides `TAKOPI_LOG_LEVEL`).
CLI flag: `--transport <id>` overrides the configured transport backend.

### `telegram/onboarding.py` - Setup validation

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
telegram/bridge.poll_updates() drains backlog, long-polls, filters chat_id == cfg.chat_id
    ↓
telegram/bridge.run_main_loop() spawns tasks in TaskGroup
    ↓
router.resolve_resume(text, reply_text) → ResumeToken | None
    ↓
router.entry_for(resume_token) or router.entry_for_engine(override/default) → RunnerEntry
    ↓
runner_bridge.handle_message() spawned as task with selected runner
    ↓
Send initial progress message (silent)
    ↓
runner.run(prompt, resume_token)
    ├── Spawns engine subprocess (e.g., codex exec --json, pi --print --mode json)
    ├── Streams JSONL from stdout
    ├── Normalizes JSONL -> takopi events
    ├── Yields Takopi events (async iterator)
    │       ↓
    │   ProgressTracker.note_event()
    │       ↓
    │   ProgressEdits best-effort transport.edit(wait=False)
    └── Ends with completed(resume, ok, answer)
    ↓
render_final() with resume line (runner-formatted)
    ↓
transport.send()/edit() final message, delete progress if needed
```

### Resume Flow

Same as above; auto-router polls all runners to extract resume tokens:
- Router returns first matching token (e.g. `` `claude --resume <id>` `` routes to Claude, `` `pi --session <path>` `` routes to Pi)
- Selected runner spawns with resume (e.g. `codex exec --json resume <token> -`, `pi --print --mode json --session <path> <prompt>`)
- Per-token lock serializes concurrent resumes on the same thread

## Error Handling

| Scenario | Behavior |
|----------|----------|
| `codex exec` fails (rc != 0) | Emits a warning `action` plus `completed(ok=false, error=...)` |
| `pi` fails (rc != 0) | Emits a warning `action` plus `completed(ok=false, error=...)` |
| Telegram API error | Logged, edit skipped (progress continues) |
| Cancellation | Cancel scope terminates the process group (POSIX) and renders `cancelled` |
| Errors in handler | Final render uses `status=error` and preserves resume tokens when known |
| No agent_message (empty answer) | Final shows `error` status |
