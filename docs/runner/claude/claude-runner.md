Below is a concrete implementation spec for adding **Anthropic Claude Code (“claude” CLI / Agent SDK runtime)** as a first-class engine in Takopi (v0.2.0).

---

## Scope

### Goal

Add a new engine backend **`claude`** so Takopi can:

* Run Claude Code non-interactively via the **Agent SDK CLI** (`claude -p`). ([Claude Code][1])
* Stream progress in Telegram by parsing **`--output-format stream-json`** (newline-delimited JSON). ([Claude Code][1])
* Support resumable sessions via **`--resume <session_id>`** (Takopi emits a canonical resume line the user can reply with). ([Claude Code][1])

### Non-goals (v1)

* Interactive Q&A inside a single run (e.g., answering `AskUserQuestion` prompts mid-flight).
* Full “slash commands” integration (Claude Code docs note many slash commands are interactive-only). ([Claude Code][1])
* MCP prompt-handling for permissions (use allow rules instead).

---

## UX and behavior

### Engine selection

* Existing: `takopi --engine codex`
* New: `takopi --engine claude`

No new CLI flags required beyond existing `--engine` unless you want convenience overrides (optional).

### Resume UX (canonical line)

Takopi appends a **single backticked** resume line at the end of the message, like:

```text
`claude --resume 8b2d2b30-...`
```

Rationale:

* Claude Code supports resuming a specific conversation by session ID with `--resume`. ([Claude Code][1])
* The CLI reference also documents `--resume/-r` as the resume mechanism.

Takopi should parse either:

* `claude --resume <id>`
* `claude -r <id>` (short form from docs)

### Permissions / non-interactive runs

In `-p` mode, Claude Code can require tool approvals. Takopi cannot click/answer interactive prompts, so **users must preconfigure permissions** (via Claude Code settings or `--allowedTools`). Claude’s settings system supports allow/deny tool rules. ([Claude Code][2])

Takopi should document this clearly: if permissions aren’t configured and Claude tries to use a gated tool, the run may block or fail.

---

## Config additions

Takopi config lives at either:

* `.takopi/takopi.toml` (project-local), or
* `~/.takopi/takopi.toml` (home). (Existing Takopi behavior.)

Add a new optional `[claude]` section.

Recommended v1 schema:

```toml
# .takopi/takopi.toml

engine = "claude"

[claude]
cmd = "claude"                       # default: "claude"
model = "claude-sonnet-4-5-20250929" # optional (Claude Code supports model override in settings too)
output_style = "Explanatory"         # optional
allowed_tools = "Bash,Read,Edit"     # optional but strongly recommended for automation
permission_mode = "acceptEdits"      # optional
max_turns = 40                       # optional safety bound
max_budget_usd = 2.50                # optional safety bound
append_system_prompt = ""            # optional
include_partial_messages = false     # optional
extra_args = []                      # optional: escape hatch
idle_timeout_s = 300                 # optional watchdog for hung processes
```

Notes:

* `--allowedTools` exists specifically to auto-approve tools in programmatic runs. ([Claude Code][1])
* Claude Code tools (Bash/Edit/Write/WebSearch/etc.) and whether permission is required are documented. ([Claude Code][2])
* Claude Code supports a permission system and multiple “modes” (plan/accept edits/bypass prompts, etc.).
* Safety bounds (`max_turns`, `max_budget_usd`) align with Agent SDK result subtypes such as `error_max_turns` and `error_max_budget_usd`. ([Claude Code][3])

---

## Code changes (by file)

### 1) `src/takopi/engines.py`

Add a new backend:

* Engine ID: `EngineId("claude")`

* `check_setup()` should:

  * `shutil.which("claude")` (or configured `cmd`) must exist.
  * Error message should include official install options and “run `claude` once to authenticate”.

    * Install methods include install scripts, Homebrew, and npm. ([Claude Code][4])
    * Agent SDK / CLI can use Claude Code authentication from running `claude`, or API key auth. ([Claude][5])

* `build_runner()` should parse `[claude]` config and instantiate `ClaudeRunner`.

* `startup_message()` e.g.:

  * `takopi (claude) is ready\npwd: ...`

### 2) New file: `src/takopi/runners/claude.py`

Implement a new `Runner`:

#### Public API

* `engine: EngineId = "claude"`
* `format_resume(token) -> str`: returns `` `claude --resume {token}` ``
* `extract_resume(text) -> ResumeToken | None`: parse last match of `--resume/-r`
* `is_resume_line(line) -> bool`: matches the above patterns
* `run(prompt, resume)` async generator of `TakopiEvent`

#### Subprocess invocation

Use Agent SDK CLI non-interactively:

Core invocation:

* `claude -p --output-format stream-json` ([Claude Code][1])

Resume:

* add `--resume <session_id>` if resuming. ([Claude Code][1])

Permissions:

* add `--allowedTools "<rules>"` if configured. ([Claude Code][1])

Prompt passing:

* Prefer writing prompt to **stdin** (supported by `-p` usage examples via piping) to avoid huge argv and leaking prompt via `ps`. ([Claude Code][1])

Other flags:

* `--permission-mode`, `--model`, `--output-style`, `--max-turns`, `--max-budget`, `--append-system-prompt`, etc. are in Claude CLI reference.

#### Stream parsing

In stream-json mode, Claude emits newline-delimited JSON objects. ([Claude Code][1])

Per the official Agent SDK TypeScript reference, message types include:

* `system` with `subtype: 'init'` and fields like `session_id`, `cwd`, `tools`, `model`, `permissionMode`, `output_style`. ([Claude Code][3])
* `assistant` / `user` messages with Anthropic SDK message objects. ([Claude Code][3])
* final `result` message with:

  * `subtype: 'success'` or error subtype(s),
  * `is_error`,
  * `result` (string on success),
  * `usage`, `total_cost_usd`, `modelUsage`,
  * `errors` list on failures,
  * `permission_denials`. ([Claude Code][3])

Takopi should:

* Parse each line as JSON; on decode error emit a warning ActionEvent (like CodexRunner does) and continue.
* Prefer stdout for JSON; log stderr separately (do not merge).

#### Mapping to Takopi events

**StartedEvent**

* Emit upon first `system/init` message:

  * `resume = ResumeToken(engine="claude", value=session_id)`
  * `title = model` (or user-specified config title; default `"claude"`)
  * `meta` should include `cwd`, `tools`, `permissionMode`, `output_style` for debugging.

**Action events (progress)**
The core useful progress comes from tool usage.

Claude Code tools list is documented (Bash/Edit/Write/WebSearch/WebFetch/TodoWrite/Task/etc.). ([Claude Code][2])

Strategy:

* When you see an **assistant message** with a content block `type: "tool_use"`:

  * Emit `ActionEvent(phase="started")` with:

    * `action.id = tool_use.id`
    * `action.kind` based on tool name:

      * `Bash` → `command`
      * `Edit`/`Write`/`NotebookEdit` → `file_change` (best-effort path extraction)
      * `WebSearch`/`WebFetch` → `web_search`
      * otherwise → `tool`
    * `action.title`:

      * Bash: use `input.command` if present
      * WebSearch: use query
      * WebFetch: use URL
      * Edit/Write: use file path
      * otherwise: tool name
    * `detail` includes a compacted copy of input (or a safe summary).

* When you see a **user message** with a content block `type: "tool_result"`:

  * Emit `ActionEvent(phase="completed")` for `tool_use_id`
  * `ok = not is_error`
  * `detail` includes a small summary (char count / first line / “(truncated)”)

This mirrors CodexRunner’s “started → completed” item tracking and renders well in existing `TakopiProgressRenderer`.

**CompletedEvent**

* Emit on `result` message:

  * `ok = (subtype == 'success' and is_error == false)`
  * `answer = result` on success; on error, a concise message using `errors` and/or denials
  * `usage` attach:

    * `total_cost_usd`, `usage`, `modelUsage`, `duration_ms`, `duration_api_ms`, `num_turns` ([Claude Code][3])
  * Always include `resume` (same session_id).

**Permission denials**
Because result includes `permission_denials`, optionally emit warning ActionEvent(s) *before* CompletedEvent:

* kind: `warning`
* title: “permission denied: <tool_name>”
  This preserves the “warnings before started/completed” ordering principle Takopi already tests for CodexRunner.

#### Session serialization / locks

Must match Takopi runner contract:

* Lock key: `claude:<session_id>` (string) in a `WeakValueDictionary` of `anyio.Lock`.
* When resuming:

  * acquire lock before spawning subprocess.
* When starting a new session:

  * you don’t know session_id until `system/init`, so:

    * spawn process,
    * wait until `system/init`,
    * acquire lock for that session id **before** yielding StartedEvent,
    * then continue yielding.

This mirrors CodexRunner’s correct behavior and ensures “new run + resume run” serialize once the session is known.

#### Cancellation / termination

Reuse the existing subprocess lifecycle pattern (like `CodexRunner.manage_subprocess`):

* Kill the process group on cancellation
* Drain stderr concurrently (log-only)
* Ensure locks release in `finally`

#### Watchdog for hung stream-json

There’s evidence that some Claude Code versions have had stream-json sessions that don’t finish cleanly (e.g., missing final result message and not terminating).

Mitigation design:

* Config `idle_timeout_s`:

  * If no stdout data is received for N seconds after init, terminate subprocess and return a failure CompletedEvent.
* Optionally turn on `include_partial_messages` for liveness signals:

  * Agent SDK defines `type: 'stream_event'` messages when enabled. ([Claude Code][3])

Default could be conservative (e.g., disabled unless configured), but the knob should exist.

---

## Bridge-level tweak (optional but recommended)

`bridge.py` fallback mismatch detector (`_resume_attempt`) only recognizes `"<engine> resume <token>"`.

If the bot is running codex but user pastes `claude --resume ...`, Takopi can’t give a good hint today.

Add a second pattern for Claude:

* `^\s*`?claude\s+(?:--resume|-r)\s+(\S+)`?\s*$`

Use it only to set `engine_hint="claude"` in the warning; still do not cross-engine resume.

---

## Documentation updates

### README

Add a “Claude Code engine” section that covers:

* Installation (install script / brew / npm). ([Claude Code][4])
* Authentication:

  * run `claude` once and follow prompts, or use API key auth (Agent SDK docs mention `ANTHROPIC_API_KEY`). ([Claude][5])
* Non-interactive permission caveat + how to configure:

  * settings allow/deny rules,
  * or `--allowedTools` / `[claude].allowed_tools`. ([Claude Code][2])
* Resume format: `` `claude --resume <id>` ``.

### `docs/developing.md`

Extend “Adding a Runner” with:

* “ClaudeRunner parses Agent SDK stream-json output”
* Mention key message types and the init/result messages.

---

## Test plan

Mirror the existing `CodexRunner` tests patterns.

### New tests: `tests/test_claude_runner.py`

1. **Contract & locking**

* `test_run_serializes_same_session` (stub `_run` like Codex tests)
* `test_run_allows_parallel_new_sessions`
* `test_run_serializes_new_session_after_session_is_known`:

  * Provide a fake `claude` executable in tmp_path that:

    * prints system/init with session_id,
    * then waits on a file gate,
    * a second invocation with `--resume` writes a marker file and exits,
    * assert the resume invocation doesn’t run until gate opens.

2. **Resume parsing**

* `format_resume` returns `claude --resume <id>`
* `extract_resume` handles both `--resume` and `-r`

3. **Translation / event ordering**

* Fake `claude` outputs:

  * system/init
  * assistant tool_use (Bash)
  * user tool_result
  * result success with `result: "ok"`
* Assert Takopi yields:

  * StartedEvent
  * ActionEvent started
  * ActionEvent completed
  * CompletedEvent(ok=True, answer="ok")

4. **Failure modes**

* `result` subtype error with `errors: [...]`:

  * CompletedEvent(ok=False)
* permission_denials exist:

  * warning ActionEvent(s) emitted before CompletedEvent

5. **Cancellation**

* Stub `claude` that sleeps; ensure cancellation kills it (pattern already used for codex subprocess cancellation tests).

6. **Idle timeout**

* Stub that prints init then hangs; ensure runner terminates and returns CompletedEvent(ok=False) when `idle_timeout_s` is set.

---

## Implementation checklist

* [ ] Add `ClaudeBackend` in `src/takopi/engines.py` and register in `ENGINES`.
* [ ] Add `src/takopi/runners/claude.py` implementing the `Runner` protocol.
* [ ] (Optional) Add bridge hint parsing for `claude --resume`.
* [ ] Add tests + stub executable fixtures.
* [ ] Update README and developing docs.
* [ ] Run full test suite.

---

If you want, I can also propose the exact **event-to-action mapping table** (tool → kind/title/detail rules) you should start with, based on Claude Code’s documented tool list (Bash/Edit/Write/WebSearch/etc.). ([Claude Code][2])

[1]: https://code.claude.com/docs/en/headless "Run Claude Code programmatically - Claude Code Docs"
[2]: https://code.claude.com/docs/en/settings "Claude Code settings - Claude Code Docs"
[3]: https://code.claude.com/docs/en/sdk/sdk-typescript "Agent SDK reference - TypeScript - Claude Docs"
[4]: https://code.claude.com/docs/en/quickstart "Quickstart - Claude Code Docs"
[5]: https://platform.claude.com/docs/en/agent-sdk/quickstart "Quickstart - Claude Docs"
