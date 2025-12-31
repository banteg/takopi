# Takopi Specification v0.2.0 [2025-12-31]

This document specifies Takopi v0.2.0 behavior and architecture in a way that is testable, evolvable, and explicitly aligned with the goals:

- **Better testability**
- **Runner abstraction** to support future runners (e.g., Claude Code)
- **Telegram remains the only bot client** (adding another is unlikely)
- **Parallel runs are allowed across different threads**, but runs for the **same thread must be serialized** to avoid corrupting history

This is a normative spec using **MUST / SHOULD / MAY** language. Sections labeled **Decision** capture choices that should remain stable unless intentionally changed.

------

## 1. Scope and goals

### 1.1 Goals (v0.2.0)

1. Provide a Telegram bot that runs an “exec agent” (runner) and streams progress updates with periodic edits.
2. Support “thread continuation” via a **resume command** embedded in chat messages.
3. Support **parallel execution across different threads** (different resume tokens).
4. Enforce **serialization per thread** (same resume token) to avoid concurrent mutation of the same engine conversation/history.
5. Establish a stable, Takopi-owned **normalized event model** that runners produce and renderers consume.
6. Keep architecture modular enough to add another runner in a future version with minimal changes.

### 1.2 Non-goals (v0.2.0)

- Adding additional bot clients besides Telegram (Discord/Slack/etc.) is out of scope.
- Implementing auto-selection of multiple runners is not required (but should be prepared for).
- Streaming partial assistant answers token-by-token is not required (progress UI is event-driven; final answer is delivered at completion).
- Supporting engines that cannot provide stable action IDs is out of scope (see §5.4).

------

## 2. Terminology

- **Runner / Engine**: Implementation that executes an agent process (Codex today; Claude Code later) and produces Takopi events.
- **Thread**: The engine-side conversation identifier. In Takopi this is represented as a **ResumeToken**.
- **ResumeToken**: A Takopi-owned structured identifier: `{ engine: EngineId, value: str }`.
- **ResumeLine**: A runner-owned string representation embedded in chat; **canonical** representation is the engine CLI command (Decision §4.1).
- **Takopi Event**: A normalized event dict emitted by a runner and consumed by renderers/bridge.
- **Progress Message**: Telegram message that is edited periodically to show live status.
- **Final Message**: Telegram message containing final answer + resume line + status.

------

## 3. Architecture overview

### 3.1 Layers and responsibilities (strict boundaries)

**Domain Model (Takopi-owned)**

- Defines: `ResumeToken`, `RunResult`, `TakopiEvent`, `Action`.
- No Telegram, no subprocess, no engine JSON.

**Runner Interface (Takopi-owned)**

- Defines `Runner` protocol: `run()`, `extract_resume()`, `format_resume()`, etc.
- Runners are trusted producers of Takopi events (Decision §5.2).

**Runner Implementations (engine-owned logic)**

- Codex runner translates engine-specific stream into Takopi events.
- Each runner enforces per-thread serialization (MUST, §6.2).

**Renderers (Takopi-owned)**

- Pure functions/state machines that consume Takopi events and produce markdown strings.
- No engine-specific parsing.
- No Telegram API calls.

**Bridge (Telegram orchestration)**

- Receives Telegram updates and turns them into runner invocations.
- Maintains throttled progress editing.
- Handles cancellation `/cancel`.
- Owns Telegram markdown constraints (limits, entity formatting).

### 3.2 Module naming and one-word modules (v0.2.0 refactor target)

Recommended module layout (single-word filenames, clean layering):

- `takopi/model.py`
  Domain types: events, actions, resume token, run result.
- `takopi/runner.py`
  Runner protocol + shared runner utilities (e.g., `EventQueue` if retained).
- `takopi/runners/codex.py`
  Codex runner implementation.
- `takopi/runners/mock.py`
  Script/mock runner for tests.
- `takopi/render.py`
  Progress renderer and event-to-text formatting.
- `takopi/bridge.py`
  Telegram orchestration; main loop and message handler.
- `takopi/cli.py`
  Typer/CLI entrypoints, config loading, engine selection.
- `takopi/markdown.py`
  Markdown sanitization + Telegram entity prep.

**Rationale:**
The normalized event model MUST NOT live under `runners/` because it is core domain state shared by bridge and renderer.

------

## 4. Resume tokens and resume lines

### 4.1 Decision: canonical resume representation is engine CLI command

The canonical representation of “resume” embedded in chat is the runner’s **engine CLI resume command**, e.g.:

- Codex: ``codex resume <uuid>``

Takopi MUST treat the runner as the authority for:

- formatting a `ResumeToken` into a `ResumeLine`
- extracting a `ResumeToken` from message text

Takopi MAY introduce additional Takopi-owned metadata lines in the future (e.g., `resume: codex:<uuid>`), but **v0.2.0 canonical remains the CLI command**.

### 4.2 ResumeToken structure (Takopi-owned)

```python
@dataclass(frozen=True, slots=True)
class ResumeToken:
    engine: str   # EngineId (string)
    value: str
```

### 4.3 Runner resume codec interface (MUST)

Each runner MUST implement:

- `format_resume(token: ResumeToken) -> str`
  Returns a ResumeLine suitable for embedding in Telegram markdown (usually inside backticks).
- `extract_resume(text: str) -> ResumeToken | None`
  Extracts a ResumeToken from arbitrary message text.
- `is_resume_line(line: str) -> bool`
  Fast check used for truncation safety (to preserve the resume line during trimming).

**Constraints:**

- `format_resume()` MUST raise or otherwise fail if `token.engine != runner.engine`.
- `extract_resume()` MUST return `None` if it cannot confidently parse a resume command for its engine.

### 4.4 Resume extraction behavior in the bridge (v0.2.0)

Given a user message `text` and optional reply-to message `reply_text`:

1. The bridge MUST attempt `runner.extract_resume(text)`.
2. If not found, the bridge MUST attempt `runner.extract_resume(reply_text)` if present.
3. If still not found, run starts as a **new thread** (`resume=None`).

**Future note (non-normative):**
For multi-runner auto-selection, the bridge MAY attempt extraction across all registered runners. This is not required for v0.2.0.

------

## 5. Normalized event model (Takopi-owned)

### 5.1 Decision: events are trusted after normalization

Runners are responsible for producing well-formed Takopi events. Downstream consumers (render/bridge) SHOULD assume validity and may fail fast if invariants are violated (Decision §5.2).

### 5.2 Event types (minimum set)

Takopi MUST support the following event types:

1. `session.started`
2. `action.started`
3. `action.completed`
4. `log`
5. `error`

### 5.3 Required fields by event type

#### 5.3.1 `session.started`

Required:

- `type: "session.started"`
- `engine: EngineId`
- `resume: ResumeToken`
- `title: str` (human-readable session/agent label)

#### 5.3.2 `action.started`

Required:

- `type: "action.started"`
- `engine: EngineId`
- `action: Action`

#### 5.3.3 `action.completed`

Required:

- `type: "action.completed"`
- `engine: EngineId`
- `action: Action`
- `ok: bool` (success/failure of the action)

#### 5.3.4 `log`

Required:

- `type: "log"`
- `engine: EngineId`
- `message: str`

Optional:

- `level: "debug" | "info" | "warning" | "error"` (default: `"info"`)

#### 5.3.5 `error`

Required:

- `type: "error"`
- `engine: EngineId`
- `message: str`

Optional:

- `detail: str` (stack trace / stderr tail)

### 5.4 Action schema (MUST, per your Decision #4)

Actions MUST have stable IDs.

```python
@dataclass(frozen=True, slots=True)
class Action:
    id: str                 # required
    kind: str               # required, stable taxonomy
    title: str              # required, short label
    detail: dict[str, Any]  # required, structured details
```

**Definition (v0.2.0):**
“Stable” means **stable within a single run**: the same underlying action MUST keep the same `Action.id` across all events in that run, and `Action.id` values MUST be unique within the run. Takopi does not require action IDs to remain stable across different runs/resumes.

Action kinds SHOULD be from a stable set (extensible):

- `command`
- `tool`
- `file_change`
- `web_search`
- `note`

Runners MAY include additional kinds, but renderers MAY treat unknown kinds as `note`.

The `detail` dict is **freeform per runner**; no per-kind schema is enforced. Renderers SHOULD handle missing or unexpected fields gracefully.

The `ok` field semantics are **runner-defined**. For example, a runner MAY treat `grep` exit code 1 (no match) as `ok=True` if contextually appropriate.

------

## 6. Runner interface and concurrency semantics

### 6.1 Runner protocol (MUST)

```python
class Runner(Protocol):
    engine: str

    async def run(
        self,
        prompt: str,
        resume: ResumeToken | None,
        on_event: Callable[[TakopiEvent], None | Awaitable[None]],
    ) -> RunResult: ...
```

### 6.2 Per-thread serialization (MUST; core invariant)

**Invariant:** At most one active run may operate on the same thread (same `ResumeToken`) at a time.

- Parallel runs are allowed only if they target **different** threads.
- Runs targeting the same thread MUST be queued and executed sequentially.
- If a run attempts to acquire the per-thread lock while another run holds it, the run MUST **queue indefinitely** until the lock is released.

**Critical requirement for new sessions:**
If `resume is None`, the runner MUST acquire the per-thread lock **as soon as the new thread's ResumeToken becomes known**, and MUST do so **before emitting `session.started`** to downstream consumers.

This prevents:

- a second run resuming the thread while the original "new session" run is still active
- history corruption due to concurrent engine operations

**Codex note (non-normative):**
For Codex, the resume token typically arrives as the first NDJSON event within ~1–2 seconds. If the subprocess exits before a resume token is observed, no `session.started` can be emitted and the bridge reports an error without a resume line.

### 6.3 RunResult (MUST)

```python
@dataclass(frozen=True, slots=True)
class RunResult:
    resume: ResumeToken      # final resume token for the run (new or existing)
    answer: str              # final assistant response text (may be empty on failure)
```

### 6.4 Event delivery semantics (MUST)

Event ordering is significant. The system MUST ensure:

- Events are delivered to `on_event` in the same order they are produced by the runner.
- Event delivery MUST NOT spawn unbounded background tasks per event.
- If `on_event` raises an exception, the runner MUST abort the run.

### 6.5 Crash and error handling

If the runner subprocess crashes or exits uncleanly:

- The bridge MUST publish an error status message.
- If `session.started` was received, the bridge MUST include the resume line in the error message.

------

## 7. Bridge (Telegram orchestration)

### 7.1 Responsibilities

The bridge MUST:

- Poll Telegram updates.
- Execute at most **16 active runs** concurrently across all threads.
- Resolve resume token (from message text or reply target).
- Start runner execution with appropriate cancellation support.
- Maintain progress rendering and Telegram edits (rate-limited).
- Publish final answer and include resume line.
- Support `/cancel` to cancel the run associated with an in-flight progress message.

**Queuing behavior:**

- Multiple prompts to the same thread are queued and executed sequentially.
- Prompts queued behind an in-flight run MUST NOT count toward the **16 active runs** limit.
- There is no queue depth limit; all prompts are accepted.

The bridge MUST NOT:

- parse engine-native events
- encode engine-specific rules beyond resume extraction via runner

### 7.2 Progress behavior

- The bridge SHOULD send an initial progress message quickly (“running…”).
- The bridge MUST edit the progress message no more frequently than `progress_edit_every` (configurable).
- The bridge SHOULD avoid edits if rendered content has not changed.

### 7.3 Resume line inclusion

The progress renderer and/or final message MUST include the canonical resume line once known:

- If `session.started` has been received, the progress view SHOULD include the resume line.
- The final message MUST include the resume line.

**Important:** because the resume line may appear during progress updates, runner-level locking for new sessions (§6.2) is REQUIRED.

### 7.4 Cancellation `/cancel`

- The bridge MUST allow the user to cancel a run in progress by sending `/cancel` in reply to the progress message (or by other defined mapping).
- Cancel MUST terminate the runner process via **SIGTERM** and stop further progress edits.
- After cancellation, the bridge MUST publish a "cancelled" status message and SHOULD include the resume line if known.
- If `/cancel` is sent with additional text, the additional text is ignored; only cancellation occurs.

### 7.5 Telegram markdown constraints

The bridge MUST:

- escape/prepare markdown per Telegram rules
- enforce Telegram message length limits (including after escaping)
- avoid truncating away the resume line (use runner `is_resume_line()`)

If truncation is required:

- the bridge MUST keep the resume line intact
- the bridge SHOULD preserve the **head** (beginning) of content and add an ellipsis marker before truncation point

------

## 8. Renderer (progress and final formatting)

### 8.1 Renderer responsibilities

Renderers MUST:

- be deterministic functions of Takopi events and internal state
- produce markdown text and (optionally) entity annotations

Renderers MUST NOT:

- depend on engine-native events
- call Telegram APIs
- perform blocking operations

### 8.2 Progress renderer state

The progress renderer SHOULD maintain:

- session title
- current running actions and their latest summaries
- completed actions and status
- latest log/error lines (bounded tail)
- resume token if known

### 8.3 Final rendering

Final output MUST include:

- status line (`done` / `error` / `cancelled`)
- final `answer`
- resume line

------

## 9. Configuration and engine selection

### 9.1 v0.2.0 behavior (Decision #5)

- A single runner/engine is selected at startup via config/CLI (default: Codex).
- Resume extraction uses only the selected runner’s parser.
- If the user attempts to resume a thread created by a different engine, resume extraction will fail and the bot treats it as a new thread.

### 9.2 Future behavior (non-normative)

Takopi MAY support:

- trying all registered runners’ `extract_resume` to auto-select a runner for resumes
- falling back to default runner when no resume is present

The architecture SHOULD keep this future change localized to a `RunnerRegistry` / router.

------

## 10. Testing requirements (v0.2.0)

### 10.1 Test categories (MUST)

1. **Runner contract tests**
   - Emits exactly one `session.started`
   - All actions have required fields and stable IDs
   - `RunResult.resume` matches session started token
   - Event ordering is preserved
   - `ok` semantics match intended behavior
2. **Per-thread serialization test (critical)**
   - Start new session run (resume=None) that emits `session.started` then blocks
   - Attempt second run using that resume token before first completes
   - Assert second run does not enter execution until first finishes
3. **Bridge progress throttling tests**
   - Edits no more frequently than configured interval
   - No edits without changes
   - Truncation preserves resume line
4. **Cancellation tests**
   - `/cancel` terminates run
   - “cancelled” status produced
   - resume line included if known
5. **Renderer formatting tests**
   - Correct rendering of actions, errors, logs
   - Stable formatting under event sequences

### 10.2 Test tooling guidelines (SHOULD)

- Provide **event factories** in tests for readability.
- Provide a deterministic fake clock/sleep.
- Use a script/mock runner to simulate event sequences.

------

## 11. Open design notes / evolution hooks

### 11.1 Takopi-owned resume tags (future discussion)

Even though canonical is engine CLI command in v0.2.0, Takopi MAY later add a Takopi-owned unambiguous line such as:

- `resume: codex:<uuid>`

Benefits:

- easier multi-runner routing
- resilience to CLI syntax changes
- simpler truncation and extraction

This is not required for v0.2.0.

### 11.2 EngineId typing

To reduce friction adding new runners, v0.2.0 SHOULD treat engine IDs as strings (or a `NewType(str)`), not a closed Literal union.

------

## 12. Changelog template (for evolving this spec)

- v0.2.0 [2025-12-31]
  - Establish Takopi normalized event model and runner protocol
  - Canonical resume representation is engine CLI command
  - Enforce per-thread serialization including new sessions once token is known
  - Telegram-only bridge with progress edits + cancellation
  - Recommended module split into one-word modules
  - Clarify: `ok` semantics are runner-defined, `detail` is freeform
  - Clarify: 16 concurrent runs limit, indefinite queue per thread
  - Clarify: SIGTERM for cancellation, `/cancel` ignores accompanying text
  - Clarify: truncation preserves head + resume line
  - Clarify: log level defaults to `info`, callback errors abort run
  - Clarify: crash publishes error with resume if known

------

## Appendix A: Example end-to-end flow (informative)

1. User sends: “Refactor this module and run tests.”
2. Bridge resolves resume token:
   - none in message, none in reply → `resume=None`
3. Bridge sends a progress message: “Running…”
4. Runner starts and emits:
   - `session.started(engine="codex", resume={engine:"codex", value:"<uuid>"})`
   - `action.started(id="1", kind="command", title="pytest", detail={...})`
   - `action.completed(id="1", ok=True, ...)`
   - `log("All tests passed")`
5. Progress renderer now includes resume line:
   - ``codex resume <uuid>``
6. User replies to progress message with follow-up prompt.
7. Bridge extracts resume via runner, chooses same thread, runner queues it behind the in-flight run if still active.
8. Final message includes:
   - “done”
   - final answer
   - resume line ``codex resume <uuid>``
