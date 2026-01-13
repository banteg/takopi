Below is a concrete implementation spec for the **Pi (pi-coding-agent CLI)** runner shipped in Takopi (v0.5.0).

---

## Scope

### Goal

Provide the **`pi`** engine backend so Takopi can:

* Run Pi non-interactively via the **pi CLI** (`pi --print`).
* Stream progress by parsing **`--mode json`** (newline-delimited JSON). Each line is a JSON object.
* Support resumable sessions via **`--session <token>`** (Takopi emits a canonical resume line the user can reply with).

### Non-goals (v1)

* Interactive TUI flows (session picker, prompts, etc.)
* RPC mode (requires a long-running process and JSON commands)

---

## UX and behavior

### Engine selection

* Default: `takopi` (auto-router uses `default_engine` from config)
* Override: `takopi pi`

### Resume UX (canonical line)

Takopi appends a **single backticked** resume line at the end of the message, like:

```text
`pi --session ccd569e0`
```

Notes:

* `pi --resume/-r` opens an interactive session picker, so Takopi uses `--session <token>` instead.
* The resume token is the **session id** (short prefix), derived from the session
  header line (`{"type":"session", ...}`) emitted to stdout in `--mode json`.
  This requires **pi-coding-agent >= 0.45.1**.
* If the path contains spaces, the runner will quote it.

### Non-interactive runs

Use `--print` and `--mode json` for headless JSONL output.

Pi does not accept `-- <prompt>` to protect prompts starting with `-`. Takopi prefixes a leading space if the prompt begins with `-` so it is not parsed as a flag.

---

## Config additions

Takopi config lives at `~/.takopi/takopi.toml`.

Add a new optional `[pi]` section.

Recommended schema:

```toml
# ~/.takopi/takopi.toml

default_engine = "pi"

[pi]
model = "..."               # optional; passed as --model
provider = "..."            # optional; passed as --provider
extra_args = []             # optional list of strings, appended verbatim
```

Notes:

* `extra_args` lets you pass new Pi flags without changing Takopi.
* Session files are stored under Pi's default session dir:
  `~/.pi/agent/sessions/--<cwd>--` (with path separators replaced by `-`).

---

## Code changes (by file)

### 1) New file: `src/takopi/runners/pi.py`

Expose a module-level `BACKEND = EngineBackend(...)`.

#### Runner invocation

The runner should launch Pi in headless JSON mode:

```text
pi --print --mode json --session <session.jsonl> <prompt>
```

When resuming, `<session.jsonl>` is replaced by the resume token extracted from the chat.

#### Event translation

Pi JSONL output is `AgentSessionEvent` (from `@mariozechner/pi-agent-core`).
The runner should translate:

* `tool_execution_start` -> `action` (phase: started)
* `tool_execution_end` -> `action` (phase: completed)
* `agent_end` -> `completed`

For the final answer, use the most recent assistant message text (from
`message_end` events). For errors, if the assistant stopReason is `error` or
`aborted`, emit `completed(ok=false, error=...)`.

---

## Installation and auth

Install the CLI globally:

```text
npm install -g @mariozechner/pi-coding-agent
```

Minimum supported pi version: **0.45.1**.

Auth is stored under `~/.pi/agent/auth.json`. Run `pi` once interactively to
set up credentials before using Takopi.

---

## Known pitfalls

* `--resume` is interactive; Takopi uses `--session <path>` instead.
* Prompts that start with `-` are interpreted as flags by the CLI. Takopi
  prefixes a space to make them safe.

---

If you want, I can also add a sample `takopi.toml` snippet to the README or
include a small quickstart section for Pi in the onboarding panel.
