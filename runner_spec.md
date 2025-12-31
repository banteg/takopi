Totally agree: if we want “Claude alongside Codex” without one being the “native” shape, we need **takopi-owned** abstractions:

* a **runner protocol** (engines are swappable),
* a **takopi event model** (formatters/renderers consume *takopi* events),
* and each engine does its own **normalization** from its stream into takopi events.

So `takopi/claude_translate.py` goes away; instead both `runners/codex.py` and `runners/claude.py` implement the same protocol and emit the same event types.

Below is a detailed spec for what `takopi/runners/base.py` should define, plus what else it should cover for v0.2.0.

---

## 1) Single Runner protocol (v0.2.0)

For v0.2.0 we keep it simple: **one runner per engine**, plus a small backend registry for setup checks and runner construction.

The runner is the only protocol and is responsible for:

* per-session serialization locks
* subprocess / SDK invocation
* streaming parsing and event emission
* final answer extraction
* cancellation handling

Setup checks (e.g., “is `codex` on PATH?”, “is the config valid?”) live outside the runner in onboarding/bridge code and are exposed via the engine backend.

**In v0.2.0**, `runners/base.py` defines the Runner protocol + shared helpers, `engines.py` registers backends, and each engine module exports a runner implementation (`codex` and `mock`).

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

## 3) Resume tokens: use runner-formatted resume commands

You called out “resume tags and resume CLI options”. The CLI flags are engine-specific, and the resume line should be formatted by the active runner.

### `ResumeToken`

```py
@dataclass(frozen=True, slots=True)
class ResumeToken:
    engine: EngineId   # "codex" | "mock" | ...
    value: str         # opaque, engine-defined
```

### Command format (v0.2.0)

Emit:

* `<engine> resume <value>` (optionally wrapped in backticks)

Examples:

* `codex resume 019b...`
* `mock resume 019b...`

### Parsing

Parser should accept:

* plain or backticked `<engine> resume <token>` lines
* If multiple resume lines exist, use the **last** one.

**Where should parsing live?**

Parsing/formatting is handled inside each runner. Since runners are engine-specific, they can interpret their own resume command format (and any legacy forms) directly. The base module does not define shared parse/format helpers.

---

## 4) Setup & discovery (outside the runner)

For v0.2.0, discovery and config validation live in onboarding/bridge code:

* check selected engine CLI is on PATH
* read `takopi.toml`
* validate `bot_token` / `chat_id`

The runner protocol does **not** include discovery APIs or data models.

---

## 5) Configuration spec: engine-specific section, but common shape

Yes: `takopi.toml` should have per-engine top-level sections that are fed into runner construction. Engine selection is via CLI flag (`--engine`), not config. There is no shared `RunnerConfig` dataclass; each runner accepts a plain mapping of its own options.

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

The bridge reads config and passes engine-specific options directly into runner construction.

---

## 6) Invocation spec: “stream events + final result” as the contract

The bridge needs:

* streaming progress events (for Telegram edits)
* final answer text
* resume token

Callback signature:

```py
EventSink = Callable[[TakopiEvent], Awaitable[None] | None]
```

Runner protocol method:

```py
async def run(
    self,
    prompt: str,
    resume: ResumeToken | None,
    on_event: EventSink | None = None,
) -> RunResult
```

`resume` is the parsed token from the user (or `None`). The bridge extracts it via `runner.extract_resume()` and passes it in.

Returns `RunResult(resume, answer, ok)`. Errors raise `RuntimeError`. Cancellation raises `CancelledError`.

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

Implementation is per-runner (e.g., a simple `dict` of `asyncio.Lock` keyed by session id).

---

## 8) What else should `base.py` cover?

Beyond the four you listed, the things that consistently matter in production:

### A) Errors

Use plain exceptions (e.g., `RuntimeError`) for invocation failures. Discovery/config errors are handled by onboarding/bridge code.

### B) Cancellation contract

Bridge workers may be cancelled on shutdown, or you may add `/cancel`.
Runners should:

* terminate subprocess / cancel SDK stream (simple `SIGTERM` is enough for Codex)
* treat cancellation as `CancelledError`
* only cancel the in-flight run for that session; queued runs should proceed afterward

### C) Output extraction contract

“Final answer” varies by engine. In v0.2.0 (Codex only), use the last `agent_message` item; there is only one final assistant message per run.

Runner must return `answer` (what we send to Telegram, not the whole transcript).

### D) Normalization rules (invariants)

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
* `TakopiEvent` (TypedDict or dataclass union)
* `Action` (TypedDict/dataclass)

### Protocols

Single protocol:

* `Runner`:

  * `engine: EngineId`
  * `run(prompt: str, resume: ResumeToken | None, on_event: EventSink | None) -> RunResult`

### Shared helpers

None required in v0.2.0.

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
* **Engine selection:** Via CLI flag (`--engine`), not config-based selection.
* **Permissions:** Controlled by Codex profile configuration, out of scope for takopi.
* **Multi-turn:** Not supported in v0.2.0. Engines run uninterrupted without mid-run user interaction.
* **Answer streaming:** Not supported. Final answer delivered only when run completes.
* **Working directories:** Single process cwd is sufficient.

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
* Resume token is emitted early via `session.started` event (not just at completion).
* `session.started` always contains a resume token; if none is produced, the run fails.
* Resume parsing uses the last matching resume line and requires strict formatting; the runner is responsible for interpreting its own resume command format.

### Error Handling & Cancellation

**Cancellation:**
* Cancellation is handled via task cancellation; the runner should terminate its subprocess (SIGTERM is enough for Codex).
* Cancel only the in-flight run for that session; queued runs should proceed afterward.

**Error capture:**
* Capture full stderr on engine invocation errors (not truncated).
* Use plain exceptions (`RuntimeError`) rather than a custom taxonomy.
* If a final answer was produced, the run succeeds even if the process exits non-zero; emit a `log` event with `level="error"` for diagnostics.

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

**Run result shape:**
* Shared `RunResult` dataclass with `resume`, `answer`, and `ok`.
* `answer` is an empty string if the engine produces no textual response.

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

**UI/UX:**
* Show engine name only at run start.
* Use the engine id string in lowercase (e.g., "codex"); no emoji.

**Context:**
* Runner is Telegram-agnostic.
* No Telegram-specific metadata in the runner interface.

### Async Contract

**Event sink:**
* Sync or async: `EventSink = Callable[[TakopiEvent], Awaitable[None] | None]`
* Runner should not apply backpressure to event emission (fire-and-forget).
* If `on_event` fails, log and continue.

**run() method:**
* `async def run(self, prompt: str, resume: ResumeToken | None, on_event: EventSink | None = None) -> RunResult`
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

### Data Models

```py
@dataclass(frozen=True, slots=True)
class ResumeToken:
    engine: EngineId
    value: str

# Events are TypedDicts or dataclasses - implementation can choose
```

### Protocols

```py
EventSink = Callable[[TakopiEvent], Awaitable[None] | None]

class Runner(Protocol):
    engine: EngineId

    async def run(
        self,
        prompt: str,
        resume: ResumeToken | None,
        on_event: EventSink | None = None
    ) -> RunResult: ...
```

### Shared Helpers

None required in v0.2.0.

---

## 13) Out of Scope for v0.2.0

The following are explicitly deferred:

* Full plugin system / dynamic backend discovery
* Claude runner implementation
* Capabilities / feature flags
* Structured error taxonomy
* Multi-turn conversations with user prompts mid-run
* Multiple working directories / workspace abstraction
* Answer streaming (incremental answer delivery)
* Permission request events
* Per-request profile selection
* Auto-retry for transient errors
* Runner timeouts / idle timeouts
* Trace capture (raw streams / stderr)
* Runner-side redaction or truncation of action titles/details
* OpenTelemetry / structured metrics
