Got it — **structlog-only**, **local-only logs**, **keep bot-token redaction**, and we explicitly want **deep visibility into subprocess JSONL + each pipeline step**, including **logging prompts (potentially INFO)**.

Below is a revised plan optimized for those goals.

---

## 1) Target state and decisions

### Target state

* **All takopi code logs via structlog** (no `logging.getLogger`, no stdlib handlers/filters).
* Logs are intended for **local debugging**: we prefer **visibility and explainability** over privacy hardening.
* We still **redact Telegram bot tokens** everywhere they might appear (URLs, payloads, errors, prompts, etc.).
* The subprocess JSONL stream becomes **traceable end-to-end**: from raw lines → msgspec parse → runner translate → event gating → progress renderer → Telegram render/send.

### Explicit non-goals (for this migration)

* No log shipping / ingestion pipeline.
* No attempt to fully capture third-party stdlib logging output (httpx internals, etc.). If we later want that, it’s a separate, deliberate decision.

---

## 2) Structlog-only logging architecture

### 2.1 One logging entrypoint

Keep `takopi.logging.setup_logging(...)` as the single entrypoint called by `cli.py` at startup, but it now configures **only structlog**.

**Responsibilities**

* Decide output format (console vs JSON lines).
* Decide levels (INFO/DEBUG).
* Install processors (timestamp, level, contextvars, exception formatting, token redaction).
* Provide a small public surface for the rest of the codebase:

  * `get_logger(__name__)` (optional convenience wrapper)
  * context utilities: `bind_run_context(...)` / `clear_context()` / `suppress_logs(...)` (see onboarding section)

### 2.2 Output format (local friendly)

Recommend supporting two formats because they solve different local-debug problems:

* **Console renderer (default)**
  Most readable when iterating quickly (especially for prompts and multi-line content).

* **JSON lines (opt-in)**
  Best when you want to grep/jq/filter and correlate many steps, especially with JSONL tracing.

**Configuration knobs (env is simplest)**

* `TAKOPI_LOG_LEVEL=INFO|DEBUG`
* `TAKOPI_LOG_FORMAT=console|json`

Keep the existing CLI `--debug` semantics by mapping it to `TAKOPI_LOG_LEVEL=DEBUG` internally.

### 2.3 Processor chain (ordered for your requirements)

Recommended processor order:

1. **merge contextvars** (so run/job context appears on every line)
2. add `timestamp`
3. add `level`
4. add `logger` (module name)
5. exception formatting (so `.exception()` produces structured info)
6. **Telegram token redaction** (must run after exception formatting)
7. renderer (console or JSON)

No “aggressive” truncation or secret scrubbing beyond the token. If you want optional truncation for sanity, make it **opt-in** (`TAKOPI_LOG_MAX_FIELD=…`), not default.

---

## 3) Minimal redaction policy: Telegram bot token only

### 3.1 Redaction scope

Redact bot token patterns in **all string fields**, including:

* URLs (`https://api.telegram.org/bot<token>/...`)
* request payloads / response bodies
* exceptions (HTTPStatusError, network errors)
* prompts / messages if they contain token-like strings

### 3.2 Implementation approach (conceptual)

* Replace the old `RedactTokenFilter` with a **structlog processor** that recursively walks the event dict and redacts strings.
* Keep the same regex patterns you already use (`bot\d+:[A-Za-z0-9_-]+`, bare token variant).
* Ensure the processor runs on the final event dict regardless of renderer mode.

### 3.3 What not to do

* Don’t try to “detect secrets” in general (API keys, prompts, etc.). You explicitly don’t want that.

---

## 4) Make JSONL debuggable end-to-end

This is the core new value you’re asking for: *“debug subprocess jsonl we get and how they are treated by each major step of our pipeline.”*

### 4.1 Define a trace identity that follows a single JSONL line

For each run, introduce:

* `run_id` (uuid or short token)
* `engine` (already exists)
* `pid` (subprocess pid)
* `jsonl_seq` (monotonic per-run counter for stdout JSONL lines)

Every log event related to processing a JSONL line includes at least:

* `run_id`, `engine`, `pid`, `jsonl_seq`

This lets you reconstruct the lifecycle of a single line precisely.

### 4.2 Add “tap points” at each pipeline step

Implementer should add logs at these stages with stable event names and consistent fields:

#### A) Subprocess lifecycle

* `subprocess.spawn` (INFO or DEBUG)

  * fields: `cmd`, `args`, `cwd` (if relevant), `env_keys` (optional), `pid`
* `subprocess.stdin.send` (INFO if you want prompts visible, else DEBUG)

  * fields: `prompt` (full string), `prompt_len`, `resume` value
* `subprocess.exit` (INFO)

  * fields: `rc`, `stderr_tail` (full tail if that’s what you want locally), `stderr_tail_lines`

#### B) Stream → line splitting (`iter_text_lines`)

You probably don’t need chunk-level logs (too noisy), but you *do* want line-level.

#### C) msgspec decode

#### D) Runner translation (`JsonlSubprocessRunner.run_impl`)

For each parsed `data` dict:

* `runner.translate.in` (DEBUG)

  * `jsonl_seq`, `data`
* `runner.translate.out` (DEBUG)

  * `jsonl_seq`, `events_count`, and a **summary** of each event (type, action_id, phase, ok)
  * optionally include the full normalized event objects too, since it’s local

For invalid JSON (`data is None`):

* `runner.jsonl.invalid` (INFO)

  * `jsonl_seq`, `line`
  * and log the resulting note/warning event you emit

#### E) Session gating / invariants (StartedEvent and CompletedEvent handling)

This is a common “why did we drop/ignore this” source of confusion — instrument it explicitly:

* `runner.started.seen`

  * fields: `resume_value`, `expected_session`, `found_session`, `emit` (bool), `reason` (`first_seen`, `duplicate`, `mismatch_expected`, etc.)
* `runner.completed.seen`

  * fields: `ok`, `has_answer`, `emit` (bool)
* `runner.drop.jsonl_after_completed`

  * log once when you start ignoring subsequent jsonl lines

#### F) Progress renderer interpretation (`ExecProgressRenderer.note_event`)

Instrument whether a normalized event affects progress display:

* `progress.note_event`

  * fields: `event_type`, plus if action: `action_id`, `kind`, `phase`, `ok`
  * `accepted` (bool)
  * if accepted: `rendered_line`
  * after update: `recent_lines` (list) and `action_count`

This tells you exactly *how* events affect the progress message.

#### G) Markdown → Telegram render + send/edit

You already log a lot of this at DEBUG today. Given your “local-only” stance, decide what to promote to INFO.

Recommended:

* Keep these **DEBUG by default**, but in `--debug` mode include full content:

  * `telegram.prepare` (markdown parts + rendered text + entity count)
  * `telegram.send_message` (chat_id, reply_to, rendered, no entities)
  * `telegram.edit_message` (chat_id, message_id, rendered, no entities)
  * `telegram.delete_message` (chat_id, message_id)

---

## 5) Prompts and payloads: logging strategy aligned to your needs

You want prompts logged, possibly INFO. Here’s a practical approach that won’t make the tool unusable:

### 5.1 What to log at INFO (recommended defaults)

* `handle.incoming` (INFO)

  * `chat_id`, `user_msg_id`, `text` (full), `resume_token` (if any), `engine_override`
* `runner.start` (INFO)

  * `engine`, `resume`, **`prompt` (full)**, `prompt_len`
* `runner.completed` (INFO)

  * `ok`, `error`, `answer_len`, `resume_value`

### 5.2 What to keep at DEBUG

* raw JSONL line logs (these can be *very* chatty)
* Telegram request/response payload dumps
* rendered markdown + entities dumps

### 5.3 Optional: “trace mode” without inventing new levels

If you want “always show JSONL pipeline even without --debug”, add one opt-in switch:

* `TAKOPI_TRACE_PIPELINE=1`
  When set, promote key JSONL logs (`jsonl.raw`, `jsonl.parse.ok`, `runner.translate.*`, `progress.note_event`) to INFO.

This avoids making default runs noisy while still letting you flip into “show me everything” mode quickly.

---

## 6) File-by-file migration checklist (structlog-only)

### Required codebase sweep: remove stdlib logging usage in takopi

Update these modules (at least — based on current repo):

* `src/takopi/logging.py`
  Replace with structlog configuration + token redaction processor + any helper context managers.

* `src/takopi/cli.py`
  Replace module logger with structlog. Ensure setup happens before anything logs.

* `src/takopi/telegram.py`
  Replace `logger.debug/error(...)` with structlog events. Keep request payload logging (since you want it locally), but ensure token redaction still applies.

* `src/takopi/utils/streams.py`
  Change `iter_jsonl` / `drain_stderr` to use a structlog logger and emit the new tap-point events (`jsonl.raw`, `jsonl.parse.ok`, etc.).

* `src/takopi/runner.py`
  Replace spawn/exit logs, add translation + gating logs, add `jsonl_seq`.

* `src/takopi/bridge.py`
  Replace runner event logging with structured events; add context binding; instrument progress renderer / telegram send/edit.

* `src/takopi/onboarding.py`
  Replace `logging.disable(...)` suppression with a structlog-native suppression mechanism (see next section).

* `src/takopi/lockfile.py`, `src/takopi/utils/subprocess.py`, runners in `src/takopi/runners/*.py`
  Replace all `logging` usage; promote prompt logging to INFO where desired (e.g., `CodexRunner.start_run`).

### Strong recommendation: standardize event naming

Use consistent dot-separated event names across modules:

* `handle.incoming`
* `subprocess.spawn`
* `jsonl.raw`
* `jsonl.parse.ok`
* `runner.translate.in`
* `runner.translate.out`
* `runner.started.seen`
* `progress.note_event`
* `telegram.send_message`
* `telegram.http_error`

This consistency matters more than perfect schemas.

---

## 7) Onboarding: replace `logging.disable` with structlog-native suppression

Right now onboarding uses `logging.disable(logging.INFO)` to keep interactive prompts clean. With structlog-only, you need an equivalent.

### Recommended approach

Implement a `takopi.logging.suppress(level="warning")` context manager that:

* sets a contextvar like `takopi_suppress_below = WARNING`
* add an early processor that checks:

  * the event’s effective level
  * the suppression threshold
  * and drops events (`DropEvent`) if below threshold

Then in onboarding:

* Replace `_suppress_logging()` with a structlog suppression context manager used around the rich/questionary UI.

This keeps interactive setup readable, even if your normal runtime logs prompts at INFO.

---

## 8) Tests and validation updates

### 8.1 Update the token-redaction test

Your existing test uses `caplog` + stdlib filter. Replace it with a structlog-native capture strategy:

Recommended options:

1. Capture structlog events in-memory (ideal for assertions on fields).
2. Or capture stdout/stderr output and assert the token never appears.

**Acceptance for the test stays the same:**

* raw token string must not appear
* redaction marker must appear (`bot[REDACTED]`)

### 8.2 Add a pipeline-trace regression test (optional but valuable)

Add a test that feeds a short JSONL fixture through a runner and asserts you emit:

* `jsonl.raw`
* `jsonl.parse.ok` / `jsonl.parse.error`
* `runner.translate.out`
  with correct `jsonl_seq` ordering.

This guards the “debuggability contract” you care about.

---

## 9) Implementation order (minimize churn, maximize safety)

1. **Replace `takopi/logging.py`** with structlog-only config + token redaction + suppression tool.
2. **Migrate `telegram.py`** first (highest chance of token leakage) + update token redaction test.
3. **Migrate JSONL plumbing**: `utils/streams.py` + `runner.py` with `jsonl_seq` and tap points.
4. **Migrate `bridge.py`** (context binding + progress/telegram instrumentation).
5. Sweep remaining modules (`cli.py`, `onboarding.py`, runners, lockfile/subprocess utils).
6. Repo-wide grep: ensure no `logging.getLogger` usage remains in `src/takopi/**`.

---

## 10) Things to pay attention to (common pitfalls)

* **Multi-line fields** (prompts, markdown, stderr tail) can make console logs “tall”. That’s OK locally, but ensure JSON mode still emits valid single-line JSON objects.
* **Token redaction must happen after exception formatting**, or tokens embedded in exception text can leak.
* **Contextvars must be cleared** at end of each run to avoid leaking `chat_id/run_id` into unrelated tasks.
* **Don’t log at import time**. Structlog config happens in CLI startup; import-time logs will render inconsistently.

