# Claude Code -> Takopi event mapping (spec)

This document specifies how to add a Claude Code runner to Takopi by translating
Claude CLI `--output-format stream-json` JSONL events into Takopi events. It is
based on the reverse-engineered schema in `humanlayer/claudecode-go`:

- `claudecode-go/types.go` (StreamEvent, Message, Content, Result)
- `claudecode-go/client.go` (CLI flags, stream parsing)
- `claudecode-go/client_test.go` (schema validation + permission_denials)

The goal is to make a Claude runner feel identical to the Codex runner from the
bridge/renderer point of view while preserving Takopi invariants (stable action
ids, per-session serialization, single completed event).

---

## 1. Input stream contract (Claude CLI)

Claude Code CLI emits **one JSON object per line** (JSONL) when invoked with
`--output-format stream-json` (only valid with `-p/--print`).

Recommended invocation (matches claudecode-go):

```
claude -p --output-format stream-json --verbose -- <query>
```

Notes:
- `--verbose` is required for `stream-json` output (clis may otherwise drop events).
- `-p/--print` is required for `--output-format` and `--include-partial-messages`.
- `-- <query>` is required to safely pass prompts that start with `-`.
- Resuming uses `--resume <session_id>` and optional `--fork-session`.
- The CLI does **not** read the prompt from stdin in claudecode-go; it passes the
  prompt as the final positional argument after `--`.

---

## 2. Resume tokens and resume lines

- Engine id: `claude`
- Canonical resume line (embedded in chat):

```
`claude --resume <session_id>`
```

Runner must implement its own regex (cannot use `compile_resume_pattern` because
that only matches `<engine> resume <token>`). Suggested regex:

```
(?im)^\s*`?claude\s+(?:--resume|-r)\s+(?P<token>[^`\s]+)`?\s*$
```

**Note:** Claude session IDs should be treated as opaque strings.

Resume rules:
- If a resume token is provided to `run()`, the runner MUST verify that any
  `session_id` observed in the stream matches it.
- If the stream yields a different `session_id`, emit a fatal error and end the run.

---

## 3. Session lifecycle + serialization

Takopi requires **serialization per session id**:

- For new runs (`resume=None`), do **not** acquire a lock until a `session_id`
  is observed (usually the first `system.init` event).
- Once the session id is known, acquire a lock for `claude:<session_id>` and hold
  it until the run completes.
- For resumed runs, acquire the lock immediately on entry.

This matches the Codex runner behavior in `takopi/runners/codex.py`.

---

## 4. Event translation (Claude JSONL -> Takopi)

### 4.1 Top-level `system` events

Claude emits a system init event early in the stream:

```
{"type":"system","subtype":"init","session_id":"...", ...}
```

**Mapping:**
- Emit a Takopi `started` event as soon as `session_id` is known.
- Optional: emit a `note` action summarizing tools/MCP servers (debug-only).

### 4.2 `assistant` / `user` message events

Claude messages include a `message` object with a `content[]` array. Each content
block can represent text, tool usage, or tool results.

For each content block:

#### A) `type = "tool_use"`
**Mapping:** emit `action` with `phase="started"`.

- `action.id` = `content.id`
- `action.kind` = map from tool name (see section 5)
- `title`:
  - if kind=`command`: use `input.command` if present
  - else: tool name or derived label
- `detail` should include:
  - `tool_name`, `tool_input`, `message_id`, `parent_tool_use_id` (if provided)

#### B) `type = "tool_result"`
**Mapping:** emit `action` with `phase="completed"`.

- `action.id` = `content.tool_use_id`
- `ok`:
  - if `content.is_error` exists and is true -> `ok=False`
  - else `ok=True`
- `detail` should include:
  - `tool_use_id`, `content` (raw), `message_id`

The runner SHOULD keep a small in-memory map from `tool_use_id -> tool_name`
(learned from `tool_use`) so the completed action title can match the started
action title.

#### C) `type = "text"`
**Mapping:**
- Default: do **not** emit an action (avoid duplicate rendering).
- Store the latest assistant text as a fallback final answer if `result.result`
  is empty or missing.

#### D) `type = "thinking"` or other unknown types
**Mapping:** optional `note` action (phase completed) with title derived from
content; otherwise ignore.

### 4.3 `result` events

The terminal event looks like:

```
{"type":"result","subtype":"success", ...}
```

**Mapping:** emit a single Takopi `completed` event:

- `ok = !event.is_error`
- `answer = event.result` (fallback to last assistant text if empty)
- `error = event.error` (if present)
- `resume = ResumeToken(engine="claude", value=event.session_id)`
- `usage = event.usage` (pass through)

#### Permission denials
`result.permission_denials` may contain tool calls that were blocked. Emit a
warning action for each denial *before* the final `completed` event:

- `action.kind = "warning"`
- `title = "permission denied: <tool_name>"`
- `detail = {tool_name, tool_use_id, tool_input}`
- `ok = False`, `level = "warning"`

### 4.4 Error handling / malformed lines

- If a JSONL line is invalid JSON: emit a warning action and continue.
- If the subprocess exits non-zero or the stream ends without a `result` event:
  emit `completed` with `ok=False` and `error` explaining the failure.
- Emit **exactly one** `completed` event per run.

---

## 5. Tool name -> ActionKind mapping heuristics

Claude tool names can evolve. The runner SHOULD map based on tool name and input
shape. Suggested rules:

| Tool name pattern | ActionKind | Title logic |
| --- | --- | --- |
| `Bash`, `Shell` | `command` | `input.command` |
| `Write`, `Edit`, `MultiEdit`, `NotebookEdit` | `file_change` | `input.path` |
| `Read` | `tool` | `Read <path>` |
| `WebSearch` | `web_search` | `input.query` |
| (default) | `tool` | tool name |

For `file_change`, emit `detail.changes = [{"path": <path>, "kind": "update"}]`.
If input indicates creation (ex: `create: true`), use `kind: "add"`.

If a tool name is unknown, map to `tool` and include the full input in `detail`.

---

## 6. Usage mapping

Takopi `completed.usage` should mirror the Claude `result.usage` object
without transformation. Optionally include `modelUsage` inside `usage` or
`detail` if downstream consumers want it (currently unused by renderers).

---

## 7. Implementation checklist (handoff)

Add a Claude runner without changing the Takopi domain model:

1. Create `takopi/runners/claude.py` implementing `Runner` and (custom)
   resume parsing.
2. Update `takopi/engines.py`:
   - add `claude` backend id
   - `check_setup`: locate `claude` binary (PATH + common locations)
   - `build_runner`: read `[claude]` config + construct runner
   - `startup_message`: `"claude is ready\npwd: <cwd>"`
3. Add new docs (this file + `claude-stream-json-cheatsheet.md`).
4. Add fixtures in `tests/fixtures/` (see below).
5. Add unit tests mirroring `tests/test_codex_*` but for Claude translation
   and resume parsing (recommended, not required for initial handoff).

---

## 8. Suggested Takopi config keys

A minimal TOML config for Claude:

```toml
[claude]
# path to claude binary (optional if on PATH)
cmd = "claude"

# model: opus | sonnet | haiku
model = "sonnet"

# optional
max_turns = 5
system_prompt = "You are a helpful coding agent"
append_system_prompt = "Follow repository AGENTS.md instructions"
allowed_tools = ["Bash", "Read", "Write", "WebSearch"]
disallowed_tools = []
permission_prompt_tool = "mcp__approvals__request_permission"

# MCP config path (JSON) or inline config (runner may write a temp file)
mcp_config_path = "~/.claude/mcp.json"

# working dir and extra dirs
working_dir = "/path/to/repo"
additional_directories = ["/path/to/other"]

# environment overrides (optional)
[claude.env]
ANTHROPIC_API_KEY = "..."
```

Mapping to CLI flags should follow `claudecode-go/client.go`.
