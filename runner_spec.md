Totally agree: if we want “Claude alongside Codex” without one being the “native” shape, we need **takopi-owned** abstractions:

* a **runner protocol** (engines are swappable),
* a **takopi event model** (formatters/renderers consume *takopi* events),
* and each engine does its own **normalization** from its stream into takopi events.

So `takopi/claude_translate.py` goes away; instead both `runners/codex.py` and `runners/claude.py` implement the same protocol and emit the same event types.

Below is a detailed spec for what `takopi/runners/base.py` should define, plus what else it should cover beyond discovery/invocation/resume/config.

---

## 1) Split the responsibilities: “Engine backend” vs “Runner instance”

It helps to separate:

### A) Backend (static-ish)

Things you want even before you can run:

* discovery/install checks
* config parsing/validation + defaults
* “how to resume” metadata

### B) Runner (stateful instance)

Things that run-time needs:

* per-session serialization locks
* subprocess / SDK invocation
* streaming parsing and event emission
* final answer extraction
* cancellation/timeout enforcement

You *can* collapse them into one class/protocol, but keeping two makes discovery/config cleaner.

**In v0.2.0**, I’d put both protocols + shared dataclasses in `runners/base.py`, and each engine module exports a backend singleton (or class) that creates runner instances.

---

## 2) Canonical “takopi events”: the key to engine replaceability

Right now, `ExecProgressRenderer` consumes Codex JSONL directly. For replaceable engines, we define a **takopi event schema** that is:

* small (only what takopi needs to render progress + final)
* stable (won’t change if an engine changes its own schema)
* expressive enough for both Codex and Claude

### Minimum event set (practical)

These are the events your renderer needs to produce the current UX:

1. **Session identified** (resume token becomes known)
2. **Action started**
3. **Action completed**
4. **Optional logs/notes** (debug, warnings)

Everything else (assistant deltas, tool IO payloads, etc.) is optional and can be added later.

### Proposed event types

All events are dict-like and must include `type` and `engine`.

```py
TakopiEventType = Literal[
  "session.started",
  "action.started",
  "action.completed",
  "log",
  "error",
]
```

#### `session.started`

Emitted once when the engine provides a session/resume token (or immediately if resuming). It must include a resume token; if none is produced, the run fails.

```py
{
  "type": "session.started",
  "engine": "codex",
  "resume": {"engine": "codex", "value": "019b..."},
}
```

#### `action.started` / `action.completed`

Actions are what show up as progress lines (“▸ …”, “✓ …”).

```py
{
  "type": "action.started",
  "engine": "codex",
  "action": {
    "id": "a-123",         # engine-provided, used to match started/completed
    "kind": "command",     # see kinds below
    "title": "git status", # what gets rendered
    "detail": {...},       # kind-specific structured info
  }
}
```

Completion:

```py
{
  "type": "action.completed",
  "engine": "codex",
  "action": {
    "id": "a-123",
    "kind": "command",
    "title": "git status",
    "detail": {"exit_code": 0},
    "ok": True,
  }
}
```

#### `log` (optional)

Non-action info.

```py
{"type":"log","engine":"codex","level":"info","message":"spawned codex pid=..."}
```

#### `error`

User-facing or renderer-facing errors. Error events may be non-fatal; a run can continue after emitting them.

```py
{"type":"error","engine":"codex","message":"codex exited rc=1", "fatal": True}
```

### Action kinds (start small)

Use kinds that map cleanly to the UI:

```py
ActionKind = Literal[
  "command",      # bash / shell
  "tool",         # generic tool call
  "file_change",  # edits/writes
  "web_search",   # searches
  "note",         # optional (plan/reasoning summary)
]
```

**Important invariant:** `action.id` is required and engine-provided; the runner does not synthesize or normalize it. IDs may repeat; renderer must handle duplicates.

---

## 3) Resume tokens: make them engine-qualified

You called out “resume tags and resume CLI options”. The CLI flags are engine-specific, but the *tag* is takopi-level.

To avoid accidentally resuming Claude with a Codex UUID, make the token explicit:

### `ResumeToken`

```py
@dataclass(frozen=True, slots=True)
class ResumeToken:
    engine: EngineId   # "codex" | "mock" | ...
    value: str         # opaque, engine-defined
```

### Tag format (v0.2.0)

Emit:

* `resume: `<engine>:<value>``

Examples:

* `resume: `codex:019b...``
* `resume: `mock:019b...``

### Parsing (back-compat)

Parser should accept:

* new: `resume: `<engine>:<token>`` (engine-qualified)
* legacy: `resume: `<uuid>``

  * treat as `codex:<uuid>` for compatibility with v0.1.x

Parsing rules:

* Only accept the strict format above (no extra whitespace inside backticks).
* If multiple resume lines exist, use the **last** one.

**Where should parsing live?**

* Put a helper in `runners/base.py` so it’s shared, not per-engine:

  * `format_resume_line(token: ResumeToken) -> str`
  * `parse_resume_from_text(text: str) -> ResumeToken | None`

Then the bridge doesn’t need to know per-engine parsing rules.

---

## 4) Discovery spec: more than “binary exists”

Discovery should answer:

* Is it installed?
* Can we run it with required features (e.g. streaming mode)?
* (Optionally) is it authenticated / configured?
* What’s the version?
* What’s the install hint?

```py
@dataclass(frozen=True, slots=True)
class EngineDiscovery:
    engine: EngineId
    installed: bool
    path: str | None = None
    version: str | None = None
    ok: bool = False                 # installed + compatible + (optional) auth ok
    problems: tuple[str, ...] = ()
    install_instructions: tuple[str, ...] = ()
```

**Why `ok` separate from `installed`?**
Because “installed but wrong version” and “installed but not logged in” are common.

Discovery belongs on the backend/protocol so onboarding can show engine-specific guidance.

---

## 5) Configuration spec: engine-specific section, but common shape

Yes: `takopi.toml` should have per-engine top-level sections that are fed into the backend. Engine selection is via CLI subcommand, not config.

Suggested config layout (v0.2.0):

```toml
bot_token = "..."
chat_id = 123

[codex]
profile = "work"
extra_args = ["-c", "notify=[]"]

[mock]
# mock-specific options
```

In `base.py`, define:

```py
@dataclass(frozen=True, slots=True)
class RunnerConfig:
    # opaque per engine; keep it typed as Mapping[str, Any] or a per-engine dataclass
    raw: Mapping[str, Any]
```

Better: each engine defines its own `CodexConfig` / `ClaudeConfig` dataclass, but the backend protocol exposes them as `Any` or `Protocol` if you want typing without circular imports.

Backend should provide:

* `parse_config(raw: Mapping[str, Any]) -> EngineConfig`
* `default_config() -> EngineConfig` (optional)
* `config_help() -> str` (optional)

---

## 6) Invocation spec: “stream events + final result” as the contract

The bridge needs:

* streaming progress events (for Telegram edits)
* final answer text
* resume token

Define a single call:

```py
@dataclass(frozen=True, slots=True)
class RunRequest:
    prompt: str
    resume: ResumeToken | None
    cwd: str
    timeout_s: float | None = None
```

```py
@dataclass(frozen=True, slots=True)
class RunResult:
    answer: str
    resume: ResumeToken | None
    cancelled: bool = False
    error: str | None = None
    wall_time_s: float | None = None
```

Callback signature:

```py
EventSink = Callable[[TakopiEvent], Awaitable[None]]
```

Runner protocol method:

```py
async def run(self, req: RunRequest, on_event: EventSink | None = None) -> RunResult
```

### NDJSON handling is an implementation detail

The protocol should *not* mandate NDJSON, only that the runner emits `TakopiEvent`s.

* Codex runner: parses its JSONL
* Claude runner: parses `stream-json` JSONL (or SDK iterator)
* Future engine: might use websockets / http streaming

---

## 7) Resume serialization & per-session queueing

You already had the per-session lock in `CodexExecRunner`. That becomes required behavior for any engine that supports resume.

Rules:

* One in-flight run per resume token.
* Calls with the same token are queued and run sequentially.
* Concurrency across different tokens is allowed.

This can be implemented as a shared helper class in `base.py`:

```py
class SessionLockRegistry:
    async def lock_for(self, key: str) -> asyncio.Lock: ...
```

So each runner can do:

* lock key = `f"{resume.engine}:{resume.value}"`

---

## 8) What else should `base.py` cover?

Beyond the four you listed, the things that consistently matter in production:

### A) Error taxonomy (user-facing vs debug)

Right now everything becomes `RuntimeError(...)`.
Define standard exceptions so the bridge can render better:

* `EngineNotInstalledError`
* `EngineMisconfiguredError`
* `EngineInvocationError` (process rc != 0, timeout, etc.)
* `EngineAuthError` (optional detection)

Each should have:

* `user_message` (safe for Telegram)
* `debug_details` (for logs)

### B) Cancellation contract

Bridge workers may be cancelled on shutdown, or you may add `/cancel`.
Runners should:

* terminate subprocess / cancel SDK stream
* return `RunResult(cancelled=True)` (standardize on this)
* be idempotent: calling `cancel()` with nothing in-flight is a no-op
* only cancel the in-flight run for that session; queued runs should proceed afterward

### C) Timeouts and “stuck stream” handling

Engines can hang waiting for permissions or network.
Standard behavior:

* `timeout_s` is enforced at runner level (no idle timeout)
* emit `error` event with `fatal=True` before returning failure

### D) Output extraction contract

“Final answer” varies by engine. In v0.2.0 (Codex only), use the last `agent_message` item; there is only one final assistant message per run.

Runner must return:

* `answer` = what we send to Telegram (not the whole transcript)

This prevents the bridge from having to understand engine semantics.

### E) Normalization rules (invariants)

Put in base as docs/tests, e.g.:

* `session.started` emitted at most once and must include a resume token (no token => run failure)
* `action.id` is required and engine-provided; IDs may repeat; runner does not synthesize or normalize them
* events are emitted in observed order (no reordering)
* `action.title` and `action.detail` are emitted in full; renderer truncates for display
* `action.completed` may appear without a prior `action.started`

---

## 9) Concretely: what `takopi/runners/base.py` should export

Here’s a crisp export list that sets us up well for v0.2.0:

### Types / constants

* `EngineId` (`Literal["codex","mock"]` for now)
* `ActionKind`, `TakopiEventType`

### Data models

* `ResumeToken`
* `EngineDiscovery`
* `RunRequest`
* `RunResult`
* `TakopiEvent` (TypedDict or dataclass union)
* `Action` (TypedDict/dataclass)

### Protocols

Option 1 (recommended): two protocols

* `EngineBackend`:

  * `engine: EngineId`
  * `discover() -> EngineDiscovery`
  * `parse_config(raw: Mapping[str, Any]) -> Any`
  * `create_runner(cfg: Any) -> Runner`

* `Runner`:

  * `engine: EngineId`
  * `run(req: RunRequest, on_event: EventSink | None) -> RunResult`

Option 2: one protocol (simpler, less clean)

* `Runner` with `@classmethod discover`, `@classmethod from_config`

### Shared helpers

* `SessionLockRegistry`
* `parse_resume_from_text(text) -> ResumeToken | None`
* `format_resume_line(token) -> str`

---

## 10) Engine normalization lives *inside each runner module*

So:

* `takopi/runners/codex.py` reads codex JSONL and emits **takopi events**
* `takopi/runners/claude.py` reads claude stream-json (or SDK stream) and emits **takopi events**

No shared `claude_translate.py`; no codex-centric "native schema". Normalization is minimal: emit events in observed order, do not synthesize IDs, and avoid reordering.

---

## 11) Interview Decisions & Clarifications (v0.2.0)

The following decisions were made through detailed discussion to clarify implementation details not fully specified above.

### Scope for v0.2.0

* **Engines:** Mock engine + Codex only. Claude runner is deferred to a later version.
* **Engine selection:** Via CLI subcommand (`takopi codex`), not config-based selection.
* **Permissions:** Controlled by Codex profile configuration, out of scope for takopi.
* **Multi-turn:** Not supported in v0.2.0. Engines run uninterrupted without mid-run user interaction.
* **Answer streaming:** Not supported. Final answer delivered only when run completes.
* **Working directories:** Single `cwd` in `RunRequest` is sufficient.

### Event Model Refinements

**Rate limiting & coalescing:**
* Runner emits all events immediately without throttling.
* Renderer is responsible for coalescing events to respect Telegram's rate limits (~1 edit/sec).

**Action identity & ordering:**
* `action.id` is required and engine-provided; runner does not synthesize it.
* `action.seq` is removed.
* IDs may repeat; renderer must handle duplicates.
* Runner emits events in observed order (no reordering).

**Payload handling:**
* `action.detail` stores untruncated payloads (generic `Mapping[str, Any]`).
* `action.title` is emitted in full.
* Renderer handles all truncation for display.
* No redaction is done in the runner.

**Parse errors:**
* Emit a `log` event with `level="error"` containing the raw unparseable line.
* Continue parsing remaining stream.

**Orphan completions:**
* `action.completed` without prior `action.started` is allowed.
* Renderer must handle orphan completions gracefully.

**No heartbeats:**
* No periodic heartbeat events for long-running actions.
* Renderer assumes action is alive until `action.completed` or `error` event.

**Error events:**
* `error` events may be emitted without failing the run.

### Session & Queue Management

**Prompt queuing:**
* When a new prompt arrives for an already-running session, queue it.
* Queue is unbounded (no max depth, no TTL).
* Queueing is per resume token and handled inside the runner; runs for the same token are sequential.
* Concurrency across different resume tokens is allowed.

**Resume behavior:**
* If a prompt is a reply to a message with a resume token, use that token; otherwise start a new session.
* No history replay on resume. Renderer starts fresh, only new actions are tracked.
* Resume token is emitted early via `session.started` event (not just in `RunResult`).
* `session.started` always contains a resume token; if none is produced, the run fails.
* Resume parsing uses the last matching resume line and requires strict formatting.

### Error Handling & Cancellation

**Cancellation:**
* Explicit `cancel()` method on Runner (not just asyncio task cancellation).
* Graceful shutdown: SIGTERM first, wait 5 seconds, then SIGKILL.
* Return `RunResult(cancelled=True)`.
* `cancel()` is idempotent; if nothing is running, it is a no-op.
* Cancel only the in-flight run for that session; queued runs should proceed afterward.

**Error capture:**
* Capture full stderr on engine invocation errors (not truncated).
* Custom exception classes defined in `base.py`:
  * `TakopiError` (base class)
  * `EngineNotInstalledError`
  * `EngineMisconfiguredError`
  * `EngineInvocationError`
  * Each has `user_message` and `debug_details` attributes.
* If a final answer was produced, the run succeeds even if the process exits non-zero; emit a `log` event with `level="error"` for diagnostics and keep `RunResult.error = None`.

**Transient errors:**
* No auto-retry. Surface errors immediately; caller implements retry logic if needed.

### Configuration

**Config file structure:**
```toml
bot_token = "..."
chat_id = 123

[codex]
profile = "work"
extra_args = ["-c", "notify=[]"]

[mock]
# mock-specific options
```

* Top-level sections per engine (`[codex]`, `[mock]`), not nested under `[engine]`.
* Profile is global config only (not per-request).

### Data Model Simplifications

**RunResult:**
* Remove `ok: bool` field.
* Use `error: str | None` where `None` means success.
* `answer` is empty string if engine produces no textual response.

**Action detail:**
* Generic `Mapping[str, Any]`, not typed per `ActionKind`.

**Action identity:**
* `action.seq` removed.
* `action.id` is required and engine-provided (may repeat).

**Environment:**
* No special env handling. Subprocess inherits parent environment naturally.

### Runner Lifecycle & Architecture

**Instantiation:**
* Singleton per engine. One runner instance reused for all requests.
* Per-runner session locks (each singleton manages its own locks).

**Discovery:**
* Async `discover()` method.
* Called on first use, not at startup.

**UI/UX:**
* Show engine name only at run start.
* Use the engine id string in lowercase (e.g., "codex"); no emoji.

**Context:**
* Runner is Telegram-agnostic.
* No Telegram-specific metadata in `RunRequest`.

### Async Contract

**Event sink:**
* Async only: `EventSink = Callable[[TakopiEvent], Awaitable[None]]`
* Runner should not apply backpressure to event emission (fire-and-forget). If `on_event` fails, the run fails.
* If fire-and-forget proves impractical during implementation, revisit this decision.

**run() method:**
* `async def run(self, req: RunRequest, on_event: EventSink | None = None) -> RunResult`
* Sufficient for all use cases. No separate start/poll pattern.

### Mock Engine

**Purpose:**
* Built-in mock engine shipped with takopi for testing and demos.

**Behavior:**
* Configurable scenarios with a loose schema (predefined sequences of events).
* Supports simulating failures (error events, non-zero exit) for testing error paths.
* Scenarios defined in config or passed programmatically.

---

## 12) Updated Protocol Summary

Based on interview decisions, here's the refined export list for `takopi/runners/base.py`:

### Types / Constants

```py
EngineId = Literal["codex", "mock"]  # "claude" added later

ActionKind = Literal[
    "command",
    "tool",
    "file_change",
    "web_search",
    "note",
]

TakopiEventType = Literal[
    "session.started",
    "action.started",
    "action.completed",
    "log",
    "error",
]
```

### Exceptions

```py
class TakopiError(Exception):
    user_message: str
    debug_details: str | None

class EngineNotInstalledError(TakopiError): ...
class EngineMisconfiguredError(TakopiError): ...
class EngineInvocationError(TakopiError): ...
```

### Data Models

```py
@dataclass(frozen=True, slots=True)
class ResumeToken:
    engine: EngineId
    value: str

@dataclass(frozen=True, slots=True)
class EngineDiscovery:
    engine: EngineId
    installed: bool
    path: str | None = None
    version: str | None = None
    ok: bool = False
    problems: tuple[str, ...] = ()
    install_instructions: tuple[str, ...] = ()

@dataclass(frozen=True, slots=True)
class RunRequest:
    prompt: str
    resume: ResumeToken | None
    cwd: str
    timeout_s: float | None = None

@dataclass(frozen=True, slots=True)
class RunResult:
    answer: str                          # empty string if no textual response
    resume: ResumeToken | None
    cancelled: bool = False
    error: str | None = None             # None means success
    wall_time_s: float | None = None

# Events are TypedDicts or dataclasses - implementation can choose
```

### Protocols

```py
EventSink = Callable[[TakopiEvent], Awaitable[None]]

class EngineBackend(Protocol):
    engine: EngineId

    async def discover(self) -> EngineDiscovery: ...
    def parse_config(self, raw: Mapping[str, Any]) -> Any: ...
    def create_runner(self, cfg: Any) -> Runner: ...

class Runner(Protocol):
    engine: EngineId

    async def run(
        self,
        req: RunRequest,
        on_event: EventSink | None = None
    ) -> RunResult: ...

    def cancel(self) -> None: ...
```

### Shared Helpers

```py
class SessionLockRegistry:
    """Per-runner lock management for session serialization."""
    async def lock_for(self, session_key: str) -> asyncio.Lock: ...

def parse_resume_from_text(text: str) -> ResumeToken | None:
    """Parse strict 'resume: `engine:value`' (or legacy 'resume: `uuid`'), using the last match."""
    ...

def format_resume_line(token: ResumeToken) -> str:
    """Format as 'resume: `engine:value`'."""
    ...
```

---

## 13) Out of Scope for v0.2.0

The following are explicitly deferred:

* Claude runner implementation
* Capabilities / feature flags
* Multi-turn conversations with user prompts mid-run
* Multiple working directories / workspace abstraction
* Answer streaming (incremental answer delivery)
* Permission request events
* Per-request profile selection
* Auto-retry for transient errors
* Trace capture (raw streams / stderr)
* Runner-side redaction or truncation of action titles/details
* OpenTelemetry / structured metrics
