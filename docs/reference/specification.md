# Takopi Specification v0.18.0 [2026-01-13]

This document is **normative**. The words **MUST**, **SHOULD**, and **MAY** express requirements.

## 1. Scope

Takopi v0.18.0 specifies:

- A **Telegram** bot bridge that runs an agent **Runner** and posts:
  - a throttled, edited **progress message**
  - a **final message** with the final answer and a resume line
- **Thread continuation** via a **resume command** embedded in chat messages
- **Parallel runs across different threads**
- **Serialization within a thread** (no concurrent runs on the same thread)
- **Automatic runner selection** among multiple engines based on ResumeLine (with a configurable default for new threads)
- A Takopi-owned **normalized event model** produced by runners and consumed by renderers/bridge

Out of scope for v0.18.0:

- Non-Telegram clients (Slack/Discord/etc.)
- Token-by-token streaming of the assistant’s final answer
- Engines/runners that cannot provide **stable action IDs** within a run

## 2. Terminology

- **EngineId**: string identifier of an engine (e.g., `"codex"`, `"claude"`, `"pi"`).
- **Runner**: Takopi adapter that executes an engine process and yields **Takopi events**.
- **Thread**: a single engine-side conversation, identified in Takopi by a **ResumeToken**.
- **ResumeToken**: Takopi-owned thread identifier `{ engine: EngineId, value: str }`.
- **ResumeLine**: a runner-owned string embedded in chat that represents a ResumeToken.
- **Run**: a single invocation of `Runner.run(prompt, resume)`.
- **TakopiEvent**: a normalized event emitted by a runner and consumed by renderers/bridge.
- **Progress message**: a Telegram message that is periodically edited during a run.
- **Final message**: a Telegram message that includes run status, final answer, and resume line.

## 3. Resume tokens and resume lines

### 3.1 Decision: canonical resume line is the engine CLI resume command

The canonical ResumeLine embedded in chat MUST be the engine’s CLI resume command, e.g.:

- `codex resume <id>`
- `claude --resume <id>`
- `pi --session <token>`

ResumeLine MUST resume the interactive session when the engine offers both interactive and headless modes. It MUST NOT point to a headless/batch command that requires a new prompt (e.g., a `run` subcommand that errors without a message).

Takopi MUST treat the runner as authoritative for:

- formatting a ResumeToken into a ResumeLine
- extracting a ResumeToken from message text

### 3.2 ResumeToken schema (Takopi-owned)

```python
@dataclass(frozen=True, slots=True)
class ResumeToken:
    engine: str  # EngineId
    value: str
```

### 3.3 Runner resume codec (MUST)

Each runner MUST implement:

* `format_resume(token: ResumeToken) -> str`
* `extract_resume(text: str) -> ResumeToken | None`
* `is_resume_line(line: str) -> bool`

Constraints:

* `format_resume()` MUST fail if `token.engine != runner.engine`.
* `extract_resume()` MUST return `None` if it cannot **confidently** parse a resume line for its engine.

### 3.4 Bridge resume resolution (MUST)

Given `text` (user message), optional `reply_text` (the message being replied to), and an ordered list of available runners `runners`:

1. The bridge MUST attempt to extract a resume token by polling all runners in order:
   1. for each `r` in `runners`, attempt `r.extract_resume(text)`
   2. choose the **first** runner that returns a non-`None` token and stop
2. If not found, it MUST repeat step (1) for `reply_text` if present.
3. If still not found, the run MUST start with `resume=None` (new thread) on the default runner (per §8, including chat-level overrides).

## 4. Normalized event model

### 4.1 Decision: events are trusted after normalization

Runners are responsible for emitting well-formed Takopi events. Consumers (renderer/bridge) SHOULD assume validity and MAY fail fast on invariant violations.

### 4.2 Supported event types (minimum set)

Takopi MUST support:

* `started`
* `action`
* `completed`

Minimal runner mode is supported:

* A runner MAY emit only `started` and `completed`.
* If `action` events are emitted, `phase="completed"` alone is valid (no requirement to emit `started`/`updated` phases).

### 4.3 Event schemas

All events MUST include `engine: EngineId` and `type`.

#### 4.3.1 `started`

Required:

* `type: "started"`
* `engine: EngineId`
* `resume: ResumeToken`

Optional:

* `title: str`
* `meta: dict`

#### 4.3.2 `action`

Required:

* `type: "action"`
* `engine: EngineId`
* `action: Action`
* `phase: "started" | "updated" | "completed"`

Optional:

* `ok: bool` (typically on `phase="completed"`)
* `message: str`
* `level: "debug" | "info" | "warning" | "error"`

Notes:

* `phase="completed"` alone is valid.

#### 4.3.3 `completed`

Required:

* `type: "completed"`
* `engine: EngineId`
* `ok: bool`          (overall run success/failure)
* `answer: str`       (final assistant answer; MAY be empty)

Optional:

* `resume: ResumeToken`   (final token; new or existing, if known)
* `error: str | None`     (fatal error message, if any)
* `usage: dict`           (telemetry/usage if available)

### 4.4 Action schema (MUST; stable IDs)

Actions MUST have stable IDs within a run:

```python
@dataclass(frozen=True, slots=True)
class Action:
    id: str
    kind: str
    title: str
    detail: dict[str, Any]
```

Stability requirements:

* Within a single run, the same underlying action MUST keep the same `Action.id` across events.
* `Action.id` values MUST be unique within a run.
* IDs do **not** need to be stable across different runs/resumes.

Action kinds SHOULD come from an extensible stable set, e.g.:

* `command`, `tool`, `file_change`, `web_search`, `subagent`, `turn`, `warning`, `telemetry`, `note`

Unknown kinds MAY be rendered as `note`.

`detail` is freeform; no per-kind schema is required.

`ok` semantics are runner-defined.

User-visible warnings/errors SHOULD be surfaced as `action` events (typically `kind="warning"` or `kind="note"`, `phase="completed"`, `ok=False`) rather than introducing new event types.

## 5. Runner protocol and concurrency

### 5.1 Runner protocol (MUST)

```python
class Runner(Protocol):
    engine: str  # EngineId

    def run(
        self,
        prompt: str,
        resume: ResumeToken | None,
    ) -> AsyncIterator[TakopiEvent]: ...
```

### 5.2 Per-thread serialization (MUST; core invariant)

Define:

* `ThreadKey(resume) := f"{resume.engine}:{resume.value}"`

Invariant:

* At most **one** active run may operate on the same `ThreadKey` at a time.

Rules:

* Runs for different ThreadKeys MAY run in parallel.
* Runs for the same ThreadKey MUST be queued and executed sequentially.
* This invariant MUST be enforced by the runner implementation even if used outside the Telegram bridge.

New thread rule (`resume is None`):

* When the runner learns the new thread’s ResumeToken, it MUST:

  * acquire the per-thread lock for that token
  * do so **before emitting** `started(resume=token)`

### 5.3 `started` emission and ordering

* If the runner obtains a ResumeToken for the run, it MUST emit exactly one `started` event containing that token.
* The runner MAY emit `action` events before `started` (e.g., pre-init warnings). Consumers MUST NOT assume `started` is the first event.

### 5.4 Completion

* If the run reaches `started`, and then terminates under the runner’s control (success or detected failure), the runner MUST emit exactly one `completed` event and it MUST be the last event.
* If the runner never obtains a ResumeToken (e.g., fatal failure before session init), it MAY emit no `started` and no `completed`.

### 5.5 Event delivery semantics (MUST)

* Events MUST be yielded in the order produced by the runner.
* The runner MUST NOT spawn unbounded background tasks per event.
* If the consumer stops iterating early (cancel/break/exception), the runner MUST abort the run best-effort and release any held locks/resources.

## 6. Bridge (Telegram orchestration)

### 6.1 Responsibilities (MUST)

The bridge MUST:

* Receive Telegram updates
* Resolve resume token (per §3.4)
* Schedule runs per thread (per §6.2)
* Start runner execution with cancellation support
* Maintain a progress message while avoiding excessive edits
* Publish a final message containing status, answer, and resume line (when known)
* Support `/cancel` for in-flight runs

The bridge MUST NOT:

* parse engine-native streams/events
* embed engine-specific rules beyond calling runner resume extraction/formatting

Queue depth:

* There is no queue depth limit; all prompts are accepted.

### 6.2 Scheduling (MUST)

Definitions:

* `Job := (chat_id, user_msg_id, text, resume: ResumeToken | None)`

Required behavior:

* For `resume != None`, the bridge MUST enqueue jobs into `pending_by_thread[ThreadKey(resume)]`.
* For each ThreadKey, exactly one worker (or equivalent mechanism) MUST drain the queue sequentially.
* A worker MUST exit when its queue is empty; the bridge SHOULD avoid retaining state for inactive threads.
* The implementation MUST avoid spawning one long-lived task per queued job (bounded concurrency).

Runs that start as new threads:

* If a job starts with `resume=None` and later yields `started(resume=token)`, the bridge MUST treat that run as the in-flight job for `ThreadKey(token)` until it completes (for scheduling and cancellation routing).

### 6.3 Progress message behavior

* The bridge SHOULD send an initial progress message quickly (e.g., “Running…”).
* The bridge SHOULD avoid excessive edits and respect transport constraints (implementation-defined).
* The bridge SHOULD skip edits when rendered content is unchanged.
* Once `started` is observed, the progress view SHOULD include the canonical ResumeLine.

### 6.4 Final message requirements (MUST)

The final output MUST include:

* a status line (`done` / `error` / `cancelled`)
* the final `answer` (if any)
* the ResumeLine if known (and MUST include it if `started` was received)

### 6.5 Cancellation `/cancel` (MUST)

* The bridge MUST allow users to cancel a run in progress by sending `/cancel` in reply to the progress message (or by an equivalent mapping defined by the bridge).
* Cancellation MUST terminate the runner process via **SIGTERM**.
* After cancellation, the bridge MUST stop further progress edits and publish a “cancelled” status message.
* The bridge SHOULD include the ResumeLine if known.
* Any additional text after `/cancel` is ignored.

### 6.6 Telegram markdown + truncation (MUST)

The bridge MUST:

* escape/prepare Telegram markdown correctly
* enforce Telegram message length limits (including after escaping)
* avoid truncating away the ResumeLine (using `runner.is_resume_line()`)

If truncation is required:

* the bridge MUST keep the ResumeLine intact
* the bridge SHOULD preserve the beginning of the content and insert an ellipsis at the truncation point

### 6.7 Crash/error handling (MUST)

If the runner crashes or exits uncleanly:

* the bridge MUST publish an error status message
* if `started` was received, the bridge MUST include the ResumeLine in that error message

## 7. Renderer

Renderers MUST:

* be deterministic functions/state machines over Takopi events + internal renderer state
* produce Telegram-ready markdown (or markdown + entities)
* tolerate `action` events that are “completed-only” (no prior `started`/`updated`)

Renderers MUST NOT:

* depend on engine-native event formats
* call Telegram APIs
* perform blocking I/O

Action update collapsing:

* If multiple `action` events share the same `Action.id`, renderers SHOULD treat later `started`/`updated` events as updates (replace the prior running line rather than appending).

## 8. Configuration and engine selection

Decision (v0.4.0):

* Takopi MUST support configuring a **default engine** used to start new threads (`resume=None`).
  * If not configured, the default engine is implementation-defined (non-normative: the reference implementation defaults to `codex`).
* If no engine subcommand is provided, Takopi MUST run in **auto-router** mode:
  * new threads use the configured default engine
  * resumed threads are routed based on ResumeLine extraction (per §3.4)
* If an engine subcommand is provided, Takopi MUST still use the auto-router, but it overrides the configured default engine for new threads.
* Resume extraction MUST poll **all** available runners (per §3.4) and route to the first matching runner.
* New thread engine override (chat-level):
* Users MAY prefix the first non-empty line with `/{engine}` (e.g. `/claude`, `/codex`, or `/pi`) to select the engine for a **new** thread.
  * The bridge MUST strip that directive from the prompt before invoking the runner.
  * If a ResumeToken is resolved from the message or reply, it MUST take precedence and the `/{engine}` directive MUST be ignored.
* Bridges MAY persist default engine overrides per Telegram scope:
  * **Topic default**: forum topic (`chat_id + thread_id`)
  * **Chat default**: chat (`chat_id`)
* When no ResumeToken is resolved, engine selection MUST follow this precedence:
  1) explicit `/{engine}` directive
  2) topic default (if any)
  3) chat default (if any)
  4) project default engine (if configured for the resolved context)
  5) global default engine

### 8.1 Command menu (Telegram)

Takopi SHOULD keep the bot’s slash-command menu in sync at startup by calling
`setMyCommands` with the canonical list of supported commands.

* The command list MUST include:
  * `cancel` — cancel the current run
  * one entry per configured engine
  * one entry per configured project alias that is a valid Telegram command
* The command list MUST NOT include commands the bot does not support.
* Command descriptions SHOULD be terse and lowercase.
* The command list SHOULD be capped at 100 entries per Telegram's limit; if the
  config exceeds that limit, implementations SHOULD warn and truncate while
  still handling all commands at runtime.

## 9. Testing requirements (MUST)

Tests MUST cover:

1. **Runner contract**

   * If a token is obtained: exactly one `started`
   * Action schema validity (required fields; stable unique IDs within run)
   * Event ordering preserved
   * `completed` emitted and last for controlled termination after `started`
2. **Runner serialization**

   * Concurrent runs for the same ResumeToken serialize
   * `resume=None` runs acquire the per-thread lock once token is known and before emitting `started`
3. **Bridge per-thread scheduling**

   * FIFO per ThreadKey
   * second job for same thread does not start until first completes
4. **Progress throttling**

   * edits not more frequent than configured interval
   * no edit when content unchanged
   * truncation preserves ResumeLine
5. **Cancellation**

   * `/cancel` terminates run and produces “cancelled”
   * ResumeLine included if known
6. **Renderer formatting**

   * completed-only actions render correctly
   * repeated events for same Action.id collapse as intended
7. **Auto-router engine selection**

   * resume lines for non-default engines are detected and routed correctly (poll all runners)
   * new threads use the configured default engine, with CLI subcommand overriding it

Test tooling SHOULD include event factories, deterministic/fake time, and a script/mock runner.

## 10. Lockfile (single-instance enforcement)

Takopi MUST prevent multiple instances from racing `getUpdates` offsets for the same bot token.

### 10.1 Lock file location

The lock file MUST be stored at `<config_path>.lock`. For the default config path, this resolves to `~/.takopi/takopi.lock`.

### 10.2 Lock file format

The lock file MUST contain JSON with:

* `pid: int` — the process ID holding the lock
* `token_fingerprint: str` — SHA256 hash of the bot token, truncated to 10 characters

### 10.3 Lock acquisition rules

* If the lock file does not exist, acquire and write the lock.
* If the lock file exists and the PID is dead (not running), replace the lock.
* If the lock file exists and the token fingerprint differs (different bot), replace the lock.
* If the lock file exists, the PID is alive, and the fingerprint matches, fail with an error instructing the user to stop the other instance.

### 10.4 Lock release

The lock file SHOULD be removed on clean shutdown. Stale locks from crashed processes are handled by the acquisition rules above.

## 11. Changelog

### v0.18.0 (2026-01-13)

- No normative changes; align spec version with the v0.18.0 release.

### v0.17.1 (2026-01-12)

- No normative changes; align spec version with the v0.17.1 release.

### v0.17.0 (2026-01-12)

- No normative changes; align spec version with the v0.17.0 release.

### v0.16.0 (2026-01-12)

- No normative changes; align spec version with the v0.16.0 release.

### v0.15.0 (2026-01-11)

- No normative changes; align spec version with the v0.15.0 release.

### v0.14.1 (2026-01-10)

- No normative changes; align spec version with the v0.14.1 release.

### v0.14.0 (2026-01-10)

- No normative changes; align spec version with the v0.14.0 release.

### v0.13.0 (2026-01-09)

- No normative changes; align spec version with the v0.13.0 release.

### v0.12.0 (2026-01-09)

- No normative changes; align spec version with the v0.12.0 release.

### v0.11.0 (2026-01-08)

- No normative changes; align spec version with the v0.11.0 release.

### v0.10.0 (2026-01-08)

- Require Telegram command menus to include valid project aliases and warn/truncate when exceeding 100 commands.

### v0.9.0 (2026-01-07)

- No normative changes; align spec version with the v0.9.0 release.

### v0.8.0 (2026-01-05)

- Add `subagent` action kind for agent/task delegation tools.
- Add lockfile specification for single-instance enforcement (§10).

### v0.7.0 (2026-01-04)

- No normative changes; implementation migrated to structlog and msgspec schemas.

### v0.6.0 (2026-01-03)

- No normative changes; added interactive onboarding and lockfile implementation.

### v0.5.0 (2026-01-02)

- No normative changes; align spec version with the v0.5.0 release.

### v0.4.0 (2026-01-01)

- Add auto-router engine selection by polling all runners to decode resume lines; add configurable default engine for new threads (subcommand overrides default).

### v0.3.0 (2026-01-01)

- Require runners to implement explicit resume formatting/extraction/detection and treat runners as authoritative for resume tokens/lines.

### v0.2.0 (2025-12-31)

- Initial minimal Takopi specification (Telegram bridge + runner protocol + normalized events + resume support).
