## Auto-router implementation plan (Takopi spec v0.4.0)

This plan is scoped to the **auto-router** behavior in the spec (primarily **§3.4** + **§8**) and how to implement it cleanly in the current repo.

---

## 1) What “done” means (acceptance criteria)

Auto-router is complete when all of the following are true:

### Engine selection

* Running **without** an engine subcommand starts Takopi in **auto-router mode**:

  * **new threads** (`resume=None`) use the configured **default engine** (spec §8)
  * **resumed threads** are routed by extracting a ResumeToken by polling **all runners** (spec §3.4)
* Running **with** an engine subcommand still uses auto-router, but the subcommand **overrides** the default engine **for new threads** (spec §8)
* Resume extraction polls runners **in a deterministic order** and uses the **first** match (spec §3.4)

### Bridge behavior

* Resume resolution checks:

  1. current message text, then
  2. replied-to message text,
  3. otherwise `resume=None` (spec §3.4)
* The bridge routes the job to the correct runner:

  * by ResumeToken.engine for resumed threads
  * by default engine for new threads
* All existing concurrency invariants and bridge behaviors remain correct (queuing per thread key, cancellation, throttled progress edits, etc.)

### Tests

* New tests cover **auto-router selection** (spec §9.7), and existing tests are updated to pass.

---

## 2) Current state vs required state (gap)

### Current repo behavior

* `cli.py` requires an explicit engine subcommand and exits otherwise.
* `BridgeConfig` contains a single `runner`.
* Resume extraction uses only `cfg.runner.extract_resume()` (single-engine).

### Needed behavior

* CLI must support:

  * `takopi` (no engine) → auto-router mode
  * `takopi codex` / `takopi claude` → auto-router mode with default override
* Bridge must have access to **multiple runners** and a **default engine**.
* Resume extraction must poll **all runners** (spec §3.4).

---

## 3) Design: add an explicit router abstraction

Introduce a small “router” component so the bridge doesn’t grow engine-specific logic.

### 3.1 New module: `src/takopi/router.py`

Create something like:

* `AutoRouter`

  * `runners: list[Runner]` (ordered list for resume extraction)
  * `runner_by_engine: dict[EngineId, Runner]`
  * `default_engine: EngineId`
  * `default_runner: Runner`

Core methods:

* `resolve_resume(text: str | None, reply_text: str | None) -> ResumeToken | None`

  * implements spec §3.4 exactly (poll list, first match)
* `runner_for(resume: ResumeToken | None) -> Runner`

  * if `resume is None` → `default_runner`
  * else → `runner_by_engine[resume.engine]` (or a friendly error if missing)
* `is_resume_line(line: str) -> bool`

  * returns `True` if **any** runner’s `is_resume_line(line)` is true
  * useful for safe prompt cleanup and truncation protection when you *don’t yet know* the engine

### 3.2 Runner ordering rule (important)

Spec requires “poll runners in order”. You need to define that order deterministically.

Recommended ordering:

1. put the **default engine runner first**
2. then append all other runners in stable sorted order (e.g., by backend id)

This makes “default engine” the most likely match if users paste multiple resume lines, while remaining deterministic.

---

## 4) Bridge changes (where auto-router becomes real)

### 4.1 Update `BridgeConfig`

Change `BridgeConfig` to carry the router (or enough to build one):

Option A (cleanest): store router

```py
@dataclass(frozen=True)
class BridgeConfig:
    bot: BotClient
    router: AutoRouter
    chat_id: int
    final_notify: bool
    startup_msg: str
    progress_edit_every: float = PROGRESS_EDIT_EVERY_S
```

Option B: store `runners` + `default_engine` and build `AutoRouter` inside `_run_main_loop`.

Plan recommendation: **Option A** (keeps bridge loop simpler and testable).

### 4.2 Resume resolution (spec §3.4)

Replace the current single-runner `_resolve_resume(...)` with router-based polling:

* In `_run_main_loop`, when a message arrives:

  * call `cfg.router.resolve_resume(text, reply_text)`
  * if token found → route to engine-specific worker queue (already keyed by `engine:value`)
  * else → start new run using default runner

### 4.3 Runner selection per job

Update `run_job(...)` and/or `handle_message(...)` so each run uses the correct runner.

Minimal intrusive pattern:

* keep `handle_message(...)` mostly the same, but pass in `runner` explicitly:

```py
await handle_message(
  cfg,
  runner=cfg.router.runner_for(resume_token),
  ...
)
```

Then inside `handle_message`, use that runner for:

* label: `working ({runner.engine})`
* `progress_renderer = ExecProgressRenderer(..., resume_formatter=runner.format_resume)`
* truncation safety: `is_resume_line = runner.is_resume_line`
* resume stripping for prompts: see next section

### 4.4 Prompt cleanup: strip resume lines across *all* engines

Right now `_strip_resume_lines(...)` is called with the single runner’s `is_resume_line`.

With auto-router, you want to strip **any engine resume command** a user might paste.

Change the prompt cleanup to use router-level detection:

* `runner_text = _strip_resume_lines(text, is_resume_line=cfg.router.is_resume_line)`

This avoids accidentally sending `claude --resume ...` as part of the prompt when the run is routed to codex, etc.

### 4.5 Truncation: preserve resume lines

For rendering/Telegram truncation, you can keep using **the selected runner’s** `is_resume_line`, because the final/progress resume line you embed will match that runner. That’s sufficient and aligns with current behavior.

(You *can* also use router-wide `is_resume_line` for truncation, but it’s not required if the output only contains one resume line.)

### 4.6 Error handling for “engine not available”

If `resolve_resume(...)` returns a token for engine `X` but `runner_by_engine` doesn’t contain it (shouldn’t happen if router polls only known runners), or runner construction failed:

* fail fast with a friendly final message:

  * status: `error`
  * include the resume line if known (spec §6.7)
  * explain that engine is unavailable / not installed / not configured

This is a UX improvement and prevents accidentally starting a new thread on the wrong engine.

---

## 5) CLI + config changes (spec §8)

### 5.1 Config: add a default engine key

Add a top-level config key (recommended name):

```toml
default_engine = "codex"

[codex]
...

[claude]
...
```

Implementation steps:

* Update `docs/readme.md` config example.
* Update `load_telegram_config` consumers to read/validate this key:

  * if present, must be a non-empty string
  * must match an available backend id
  * if missing → fallback to implementation-defined default (spec suggests `codex`)

### 5.2 CLI behavior updates (`src/takopi/cli.py`)

Goals:

* `takopi` → auto-router mode
* `takopi codex` / `takopi claude` → auto-router mode with default override

Implementation approach:

1. Replace `_run_engine(engine=..., ...)` with `_run_auto_router(default_engine_override=..., ...)`.
2. Build **all runners** from discovered backends once:

   * `backends = list_backends()`
   * for each backend, get engine config table and `build_runner(...)`
3. Determine default engine:

   * `default_engine = override if provided else config.get("default_engine") else "codex"`
4. Create router:

   * runner ordering: default runner first, then others sorted
5. Build `BridgeConfig(router=..., ...)` and start `_run_main_loop`.

### 5.3 Onboarding checks

Right now `check_setup(backend)` validates a single engine command exists + config is present.

For auto-router:

* **MUST** validate config exists + bot_token/chat_id are valid (already)
* **MUST** validate the chosen default engine is usable (at least binary on PATH)
* **SHOULD** warn (non-fatal) if other engines are missing binaries, since they’ll only matter if you try to resume them

Plan:

* Add `check_setup_auto_router(default_backend, all_backends)` that:

  * errors if default engine missing
  * collects warnings for other missing engines and logs or prints a short note in startup

### 5.4 Startup message update

Currently: `agent: backend.id`

Auto-router should message something like:

* `default agent: <engine>`
* `mode: auto-router`
* `engines: codex, claude`

(Keep it short, but clarify behavior.)

---

## 6) Tests (spec §9.7 specifically)

Add tests that validate selection and precedence without needing subprocess binaries.

### 6.1 New unit tests for router logic

Create `tests/test_auto_router.py` (or extend `test_exec_bridge.py`):

Test cases:

1. **Resume in message text is detected**

   * message contains `` `claude --resume abc` ``
   * router resolves `ResumeToken(engine="claude", value="abc")`
2. **Resume in reply_text is detected**

   * message has no resume, reply_text does
3. **Polling order chooses first matching runner**

   * craft text containing both a codex and claude resume line
   * ensure the runner ordering rule picks the intended one
4. **Fallback to None**

   * no resume line anywhere → returns None

Also add missing coverage for Claude resume parsing:

* accepts `` `claude --resume <id>` ``
* accepts `claude -r <id>`
* ignores malformed lines

### 6.2 Bridge integration tests: correct runner invoked

Use `ScriptRunner` (from `takopi.runners.mock`) to avoid subprocesses.

Pattern:

* create two ScriptRunner instances with engines `"codex"` and `"claude"`
* build router with default `"codex"`
* feed a message containing a resume line for `"claude"` (using a runner that can parse it; either:

  * use real `ClaudeRunner.extract_resume`, or
  * configure a MockRunner to match the claude syntax)
* assert the `"claude"` runner’s `.calls` received the run

### 6.3 CLI tests updates

Update `tests/test_engine_discovery.py` expectations:

* root `takopi` should no longer exit with “choose engine”
* engine subcommands still exist and still work, but they route through auto-router

If you change option placement (group vs subcommand), update tests accordingly.

---

## 7) Documentation updates

Minimum doc changes to keep repo consistent with spec:

* `readme.md`

  * usage: add `takopi` (no engine) as recommended default
  * explain that `takopi codex` sets default for new threads, but resumes route automatically
  * add `default_engine` config key example

* `docs/developing.md`

  * update the “Resume parsing” note: now it’s **cross-engine fallback via polling all runners**
  * update data flow diagram to show router selection

* `changelog.md`

  * add unreleased entry: auto-router + configurable default engine

---

## 8) Implementation sequence (low-risk order)

1. **Add `AutoRouter` module + tests** (pure logic, easy to validate)
2. **Refactor bridge** to accept router + per-job runner selection

   * keep scheduling/cancel logic intact
3. **Update CLI** to build router and run without subcommand
4. **Update onboarding** for default engine + optional warnings for others
5. **Update docs + changelog**
6. Run full suite: `make check` (or `uv run pytest`, `ruff`, `ty`)

---

## 9) Notable edge cases (and how the plan handles them)

* **User pastes a resume line into prompt**
  → router-wide `_strip_resume_lines` removes it regardless of engine.

* **Message contains resume lines for multiple engines**
  → deterministic runner ordering governs the selection (spec requires “first match”).

* **Engine installed mismatch (resume for an engine not installed)**
  → router can still parse it; run will fail when spawning the subprocess. Prefer improving UX by detecting missing binaries at startup and/or emitting a friendly “engine unavailable” error when routed.

* **Existing per-thread scheduling**
  → unchanged, because thread keys already include `engine:value`, and StartedEvent carries the engine in the token.

---

If you want, I can also provide a concrete “diff-style” checklist mapped to files (e.g., `cli.py`, `bridge.py`, new `router.py`, and exact test files to touch), but the above is the full implementation plan aligned to the specification.
