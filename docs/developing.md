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

**Key patterns:**
- Bridge schedules runs FIFO per thread to avoid concurrent progress messages; runner locks enforce per-thread serialization
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

### `render.py` - Takopi event rendering

Transforms takopi events into human-readable text:

| Function/Class | Purpose |
|----------------|---------|
| `ExecProgressRenderer` | Stateful renderer tracking recent actions for progress display |
| `render_event_cli()` | Format a takopi event for CLI logs |
| `format_elapsed()` | Formats seconds as `Xh Ym`, `Xm Ys`, or `Xs` |

**Supported event types:**
- `started`
- `action`
- `completed`

### `model.py` / `runner.py` - Core domain types

| File | Purpose |
|------|---------|
| `model.py` | Domain types: resume tokens, actions, events, run result |
| `runner.py` | Runner protocol + event queue utilities |

### `engines.py` - Engine backend registry

Registers available engines and provides setup checks + runner construction.

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

1. Implement the `Runner` protocol in `src/takopi/runners/<engine>.py`.
2. Emit Takopi events from `takopi.model` and implement resume helpers
   (`format_resume`, `extract_resume`, `is_resume_line`).
3. Register an `EngineBackend` in `src/takopi/engines.py` with setup checks
   and runner construction.
4. Extend tests (runner contract + any engine-specific translation tests).

### Example: adding a `pi` engine

This is a concrete walkthrough for an imaginary CLI called `pi`. The goal is to
make it easy to drop in another engine without changing the Takopi domain model.

#### 1) Decide engine identity + resume format

- Engine id: `"pi"` (used in config, resume tokens, and CLI subcommand).
- Canonical resume line: the engine’s own CLI resume command, e.g.
  `` `pi --resume <session_id>` ``.
- If your engine uses the standard `"<engine> resume <token>"` format, you can
  reuse `compile_resume_pattern()`. Otherwise, define a custom regex in the
  runner (like Claude does).

#### 2) Implement `src/takopi/runners/pi.py`

Skeleton outline:

```py
ENGINE: EngineId = "pi"
_RESUME_RE = re.compile(r"(?im)^\s*`?pi\s+--resume\s+(?P<token>[^`\\s]+)`?\\s*$")

@dataclass
class PiRunner(SessionLockMixin, ResumeTokenMixin, Runner):
    engine: EngineId = ENGINE
    resume_re: re.Pattern[str] = _RESUME_RE

    pi_cmd: str = "pi"
    model: str | None = None
    allowed_tools: list[str] | None = None

    def _build_args(self, prompt: str, resume: ResumeToken | None) -> list[str]:
        args = ["--jsonl"]
        if resume is not None:
            args.extend(["--resume", resume.value])
        if self.model is not None:
            args.extend(["--model", self.model])
        if self.allowed_tools:
            args.extend(["--allowed-tools", ",".join(self.allowed_tools)])
        args.append("--")
        args.append(prompt)
        return args

    async def run(
        self, prompt: str, resume: ResumeToken | None
    ) -> AsyncIterator[TakopiEvent]:
        async for evt in self._run_with_resume_lock(prompt, resume, self._run):
            yield evt
```

Key implementation notes:

- Use `SessionLockMixin` to enforce per-session serialization.
- Use `ResumeTokenMixin` for `format_resume` / `extract_resume` / `is_resume_line`.
- Use `iter_jsonl(...)` + `drain_stderr(...)` from `takopi.utils.streams`.
- **Do not truncate** tool outputs in the runner; pass full strings into events.
  Truncation belongs in renderers.

#### 3) Map Pi JSONL → Takopi events

Example Pi lines (imaginary):

```json
{"type":"session.start","session_id":"pi_01","model":"pi-large"}
{"type":"tool.use","id":"toolu_1","name":"Bash","input":{"command":"ls"}}
{"type":"tool.result","tool_use_id":"toolu_1","content":"ok","is_error":false}
{"type":"final","session_id":"pi_01","ok":true,"answer":"Done."}
```

Mapping guidance:

- `session.start` → `StartedEvent(engine="pi", resume=<session_id>, title=<model>)`
- `tool.use` → `ActionEvent(phase="started")`
- `tool.result` → `ActionEvent(phase="completed")` and **pop** pending actions
- `final` → `CompletedEvent(ok, answer, resume)` (emit **exactly one**)

If Pi emits warnings/errors before the final event, surface them as completed
`ActionEvent`s (e.g., `kind="warning"`).

#### 4) Register engine in `src/takopi/engines.py`

Add:

- `_pi_check_setup()` that verifies `pi` exists on PATH
- `_pi_build_runner()` that reads `[pi]` config and returns `PiRunner`
- A new `EngineBackend(id="pi", display_name="Pi", ...)` entry

Example config (minimal):

```toml
[pi]
model = "pi-large"
allowed_tools = ["Bash", "Read"]
```

#### 5) Add CLI subcommand

Expose `takopi pi` alongside `takopi codex` / `takopi claude` by adding a new
`@app.command()` in `src/takopi/cli.py`.

#### 6) Tests + fixtures

- Add `tests/test_pi_runner.py` for translation behavior.
- Reuse `tests/test_runner_contract.py` to ensure lock/resume invariants.
- Add JSONL fixtures under `tests/fixtures/` for the Pi stream.

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

Same as above, but:
- Runners parse resume lines (e.g. `` `codex resume <token>` ``)
- Command becomes: `codex exec --json resume <token> -`
- Per-token lock serializes concurrent resumes

## Error Handling

| Scenario | Behavior |
|----------|----------|
| `codex exec` fails (rc != 0) | Emits a warning `action` plus `completed(ok=false, error=...)` |
| Telegram API error | Logged, edit skipped (progress continues) |
| Cancellation | Cancel scope terminates the process group (POSIX) and renders `cancelled` |
| Errors in handler | Final render uses `status=error` and preserves resume tokens when known |
| No agent_message (empty answer) | Final shows `error` status |
