# Permission Management from Telegram

**Date:** 2026-03-21
**Branch:** `feat/interactive-prompts`
**Related issues:** #195, #207, #116, #206

---

## Context & Findings

### The Problem

Takopi users have no visibility or control over engine permission settings from Telegram. When a tool is denied, the session either hangs indefinitely (the original #195 bug) or completes with cryptic error text that doesn't explain what happened or how to fix it. Users resort to `dangerously_skip_permissions` as a blunt workaround.

### What We Investigated

We tested the Claude Code CLI's `control_request` protocol extensively and discovered:

1. **`-p` mode never emits `control_request` events.** Claude auto-denies unapproved tools internally, returning an error `tool_result` like "Claude requested permissions to use X, but you haven't granted it yet." No interactive approval event reaches stdout.

2. **The Agent SDK protocol (`--permission-prompt-tool stdio`) is broken.** [SDK issue #469](https://github.com/anthropics/claude-agent-sdk-python/issues/469) confirms that `can_use_tool` callbacks never fire, reproduced across CLI versions 2.1.6 through 2.1.73 (March 2026). Multiple users confirmed.

3. **Claude's plan mode (`ExitPlanMode`) deadlocks in headless mode.** `ExitPlanMode` is a `tool_use` that triggers a `PermissionRequest` dialog — a separate system from `control_request`. It doesn't fire in `-p` mode, and even `PermissionRequest` hooks don't reliably work ([Claude issue #15755](https://github.com/anthropics/claude-code/issues/15755)).

4. **Codex has no interactive approval in `exec --json` mode.** Approval events are silently dropped by the JSONL processor. Declined commands surface as `status: "declined"` on completed items. Interactive approval only exists in `codex app-server` (JSON-RPC 2.0), which is a completely different protocol.

5. **`permission_denials` data exists but is invisible.** Claude's `result` event includes a `permission_denials` array with tool names and inputs, but takopi's `StreamResultMessage` schema doesn't declare the field — msgspec silently drops it.

6. **The Untether fork's approach is fragile.** Their 2,067-line Claude runner with PTY-based stdin, global registries, and plan mode state machine has [open bugs where the event loop freezes](https://github.com/littlebearapps/untether/issues/156) after permission requests.

### What Works Today

| Mechanism | Claude | Codex |
|---|---|---|
| Pre-approve tools before run | `--allowedTools` | `--sandbox`, `-c sandbox_permissions` |
| Permission mode | `--permission-mode acceptEdits\|bypassPermissions` | `-c approval_policy=never` (exec default) |
| Web search | `--allowedTools WebSearch` | `-c web_search=live` |
| Network access | Built-in (always available) | `-c sandbox_permissions=["network-full-access"]` |
| Per-tool control | Fine-grained (`Bash`, `Read`, `mcp__server__*`) | Coarse (sandbox mode, network on/off) |
| Mid-session changes | No (next run only) | No (next run only) |
| Interactive approval | Broken (SDK #469) | Not available in exec mode |

### Design Principles

1. **Work with what the engines give us today** — don't depend on broken protocols
2. **Pre-run configuration over mid-run interactivity** — both engines only support config at launch
3. **Make denials visible and actionable** — show what was denied, offer one-tap fix
4. **Engine-agnostic command, engine-specific behavior** — `/tools` works for all engines
5. **Incremental delivery** — Phase A is independently valuable, B and C build on it

---

## Architecture Overview

```
User in Telegram
    │
    ├── /tools claude add WebSearch     ← Phase A: manage allowed tools
    ├── /tools codex search live        ← Phase A: manage codex settings
    │
    ▼
EngineOverrides (topic scope → chat scope → config → default)
    │  Stored in TopicStateStore (per-topic) and ChatPrefsStore (per-chat)
    │  Topic overrides take precedence, same as /model and /reasoning
    │
    ├── claude: { allowed_tools: [...], permission_mode: "acceptEdits" }
    ├── codex:  { search: "live" }  ← only explicitly set values; rest inherited
    │
    ▼
Engine runner (build_args)
    │
    ├── claude: --allowedTools Bash,Read,Edit,Write,WebSearch --permission-mode acceptEdits
    ├── codex:  -c web_search=live  ← sandbox/network omitted when inherited
    │
    ▼
Stream-json output
    │
    ├── permission_denials in result event  ← Phase A: surface as warnings
    ├── denied tool names                   ← Phase B: inline "Add" buttons
    │
    ▼
Telegram progress message
    │
    ├── "⚠ WebSearch denied"               ← Phase A: visible in progress
    ├── [Add to allowed] button             ← Phase B: one-tap fix
    └── Plan review + approve flow          ← Phase C: system-prompt plan mode
```

---

## Phase A: Denial Visibility + `/tools` Command

**Goal:** Users see what was denied and can fix it for next run.

### A1. Surface `permission_denials` as Warning Events

**Problem:** `StreamResultMessage` doesn't declare `permission_denials`, so msgspec drops it.

**Changes:**

`schemas/claude.py` — add field to `StreamResultMessage`:
```python
permission_denials: list[dict[str, Any]] | None = None
```

`runners/claude.py` — in `translate_claude_event`, after handling `StreamResultMessage`, emit warning `ActionEvent`s for each denial before the `CompletedEvent`:
```python
case claude_schema.StreamResultMessage(permission_denials=denials, ...) if denials:
    events = []
    for denial in denials:
        tool_name = denial.get("tool_name", "unknown")
        events.append(factory.action(
            phase="completed",
            action_id=f"claude.denied.{tool_name}",
            kind="warning",
            title=f"denied: {tool_name}",
            detail=denial,
        ))
    events.append(factory.completed(...))  # existing completed event
    return events
```

`model.py` — `"warning"` is already in `ActionKind` (if not, add it).

The `ProgressTracker` already passes through `"warning"` kind events (it only filters `"turn"` and `"prompt"`), so these will appear in the progress message automatically.

For Codex, `status: "declined"` on command execution items already surfaces as `ok=False`. No schema change needed, but we should ensure the declined tool name appears in the event title.

**Files changed:** `schemas/claude.py`, `runners/claude.py` (+ possibly `runners/codex.py`)
**Tests:** Add fixture with `permission_denials`, test warning events are emitted.

### A2. Add `permission_mode` Config for Claude

**Problem:** Users with `defaultMode: "plan"` in `~/.claude/settings.json` get stuck.

**Changes:**

`runners/claude.py` — add field to `ClaudeRunner`:
```python
permission_mode: str | None = None
```

In `_build_args`, after the `--allowedTools` block:
```python
if self.permission_mode is not None:
    args.extend(["--permission-mode", self.permission_mode])
```

In `build_runner`:
```python
permission_mode = config.get("permission_mode")
```

**Config:**
```toml
[claude]
permission_mode = "acceptEdits"
```

**Files changed:** `runners/claude.py`
**Tests:** Test `_build_args` includes `--permission-mode` when set.

### A3. Add Codex Sandbox/Search/Network Config

**Problem:** Takopi passes no sandbox settings to Codex, relying entirely on user's config file. Users have no way to adjust these from Telegram.

**Changes:**

`runners/codex.py` — add fields to `CodexRunner`:
```python
sandbox_mode: str | None = None       # workspace-write | read-only | danger-full-access
web_search: str | None = None         # live | cached | disabled
network_access: bool | None = None    # enables network-full-access sandbox permission
```

In `build_args`, inject `-c` flags only when explicitly set (inherit from codex config by default):
```python
if self.sandbox_mode is not None:
    args.extend(["-c", f"sandbox_mode={self.sandbox_mode}"])
if self.web_search is not None:
    args.extend(["-c", f"web_search={self.web_search}"])
if self.network_access is True:
    args.extend(["-c", 'sandbox_permissions=["network-full-access"]'])
```

**Network access is enable-only.** Codex's `sandbox_permissions` is an additive grant list — there is no CLI mechanism to force-disable network if the user's `~/.codex/config.toml` already enables it. When `network_access` is `None` or `False`, takopi simply omits the flag and inherits whatever the user's Codex config provides. The `/tools` command surfaces this honestly (see display format below).

At runtime, `EngineRunOptions` values (from Telegram `/tools` overrides) take precedence over static config, which takes precedence over Codex's own config. When nothing is set, Codex uses its own defaults.

In `build_runner`, read from config:
```python
sandbox_mode = config.get("sandbox_mode")
web_search = config.get("web_search")
network_access = config.get("network_access")
```

**Config (optional — all default to inherit):**
```toml
[codex]
# sandbox_mode = "workspace-write"  # uncomment to override codex config
# web_search = "live"               # uncomment to enable web search
# network_access = false            # uncomment to control network access
```

**Files changed:** `runners/codex.py`
**Tests:** Test `build_args` includes correct `-c` flags.

### A4. `/tools` Telegram Command

**Problem:** No way to manage engine permissions from Telegram.

**Command syntax:**

```
/tools                              → show settings for current/default engine
/tools claude                       → show claude allowed_tools + permission_mode
/tools claude add WebSearch         → add to allowed_tools
/tools claude add mcp__context7__*  → allow all tools from MCP server
/tools claude remove Bash           → remove from allowed_tools
/tools claude set Read,Write,Edit   → replace entire list
/tools claude mode acceptEdits      → set permission_mode
/tools codex                        → show codex sandbox/search/network
/tools codex sandbox write          → workspace-write
/tools codex sandbox full           → danger-full-access
/tools codex sandbox readonly       → read-only
/tools codex search live            → enable web search
/tools codex network on             → grant network access for this chat
/tools codex network off            → stop granting (inherit from codex config)
```

Note: `/tools codex network off` removes takopi's network grant — it does **not** force-disable network if the user's Codex config already enables it. Codex's sandbox_permissions model is additive; takopi can grant access but cannot revoke what the user's own config provides.

**Display format** (for `/tools claude`):
```
Claude tools:
  Allowed: Bash, Read, Edit, Write (override)
  Mode: acceptEdits (config)
  MCP servers: context7, github

  /tools claude add <tool>
  /tools claude add mcp__<server>__*
```

Each setting shows its source: `(override)` for Telegram-set values, `(config)` for `takopi.toml`, `(default)` for code fallback. For Codex:
```
Codex settings:
  Sandbox: inherited
  Search: live (override)
  Network: inherited

  /tools codex sandbox write|full|readonly
  /tools codex search live|cached|disabled
  /tools codex network on|off
```

"inherited" means takopi passes no flag and Codex uses its own config. For network, `on` grants access via takopi; `off` removes the grant (but cannot override Codex's own config if it already enables network).

**Persistence:** Add to `EngineOverrides` struct in `engine_overrides.py`:
```python
class EngineOverrides(msgspec.Struct, forbid_unknown_fields=False):
    model: str | None = None
    reasoning: str | None = None
    allowed_tools: list[str] | None = None
    permission_mode: str | None = None
    # Codex-specific (None = inherit from codex config)
    sandbox_mode: str | None = None
    web_search: str | None = None
    network_access: bool | None = None  # True = grant; None/False = inherit
```

These override the static config values from `takopi.toml` at runtime. Telegram overrides take precedence over config file values, with topic-over-chat precedence (same as `/model` and `/reasoning`).

**Runtime flow:**
1. `/tools claude add WebSearch` → handler reads current effective list, appends `WebSearch`, writes full list to `EngineOverrides.allowed_tools` via `apply_engine_override()` (topic-scoped when in a topic, chat-scoped otherwise)
2. Next run: `_resolve_engine_run_options()` merges topic + chat overrides (topic wins)
3. `EngineRunOptions` carries the `allowed_tools` override to the runner
4. Runner's `_build_args` uses the override if set, otherwise falls back to static config

This requires extending `EngineRunOptions` to carry tool settings:
```python
@dataclass(frozen=True, slots=True)
class EngineRunOptions:
    model: str | None = None
    reasoning: str | None = None
    allowed_tools: list[str] | None = None
    permission_mode: str | None = None
    sandbox_mode: str | None = None
    web_search: str | None = None
    network_access: bool | None = None
```

**New files:**
- `telegram/commands/tools.py` — command handler

**Modified files:**
- `ids.py` — add `"tools"` to `RESERVED_CHAT_COMMANDS`
- `telegram/loop.py` — add dispatch branch in `_dispatch_builtin_command`
- `telegram/commands/handlers.py` — re-export
- `telegram/commands/menu.py` — add to bot command list
- `telegram/engine_overrides.py` — extend `EngineOverrides`
- `runners/run_options.py` — extend `EngineRunOptions`
- `runners/claude.py` — read tool settings from `run_options`
- `runners/codex.py` — read sandbox/search/network from `run_options`

**Tests:** Command parsing, override persistence, integration with runner args.

### A5. Show MCP Server Names in `/tools` Output

The `system/init` message includes `mcp_servers: [{name, status}]`. We can capture this during the first event of a session and display it in `/tools` output, so users know what servers are available for wildcard allows.

This is informational only — no new protocol needed. The data is already in `StreamSystemMessage.mcp_servers`.

**Files changed:** Minimal — read `mcp_servers` from init event, store on `ClaudeStreamState`, surface in `/tools` display (could be deferred if complex).

---

## Phase B: Reactive Denial Notifications with Inline Buttons

**Goal:** When a tool is denied, offer one-tap fix.

**Depends on:** Phase A (denial visibility, `/tools` persistence)

### B1. Denial Notification Messages

When a `permission_denials` warning event is emitted, instead of just showing it in the progress message, send a separate Telegram message with inline keyboard buttons:

```
⚠ WebSearch denied (not in allowed tools)
[Add WebSearch]  [Add all from server]
```

For MCP tools:
```
⚠ mcp__context7__resolve-library-id denied
[Add this tool]  [Add all context7]
```

**Callback format:**
```
takopi:addtool:{engine}:{chat_msg_id}:{tool_pattern}
```

**Handler:** On button press:
1. Read current effective allowed_tools for the engine
2. Append the tool pattern, write the full list via `apply_engine_override()` (topic-scoped when the callback originates from a topic, chat-scoped otherwise — same scoping as `/tools`)
3. Edit the message to confirm: "Added WebSearch to allowed tools"
4. Clear inline keyboard

**Limitation:** This fixes the NEXT run, not the current one. The message should say "Added for future runs" to set expectations.

### B2. Denial Summary in Completed Message

After a run completes with denials, append a summary line to the final message:
```
⚠ 2 tools denied: WebSearch, mcp__context7__resolve-library-id
```

This uses the denial data already captured in Phase A.

**Files changed:** `runner_bridge.py` (final message rendering), `telegram/bridge.py` (new callback handler), `telegram/loop.py` (callback routing)

---

## Phase C: Soft Plan Mode + Extended Settings

**Goal:** Enable plan-review workflow without relying on broken `ExitPlanMode`.

**Depends on:** Phase A + B

### C1. System-Prompt Plan Mode

Instead of `--permission-mode plan` (which deadlocks), inject planning instructions via `--append-system-prompt`:

```
Before making any code changes, first produce a detailed plan.
Present the plan as a numbered list of steps.
After presenting the plan, stop and wait for the user to approve it
before proceeding with implementation.
```

**UX flow:**
1. User sends `/plan on` (or `/tools claude plan on`)
2. Takopi stores the plan preference in `ChatPrefsStore`
3. Next run: `--append-system-prompt` with planning instructions
4. Claude produces a plan in the assistant text (visible in progress message)
5. Claude stops (either naturally or by calling `AskUserQuestion`)
6. User reviews the plan in Telegram, sends "approved" or "change X" as a follow-up message
7. Claude continues with implementation in the same session (via `--resume`)

**Why this works:** No `ExitPlanMode`, no `control_request`, no blocking. Claude's text output is already rendered in the progress message. The "approval" is just a normal follow-up message in the conversation.

**Limitation:** Claude might not always stop after the plan (it's a soft instruction, not an enforcement). To mitigate: use `--max-turns 1` for the planning phase, then `--resume` with approval for the execution phase. Or use a `PreToolUse` hook that blocks Write/Edit/Bash during planning.

### C2. Codex App-Server Mode (Future)

If per-command interactive approval is needed for Codex, this requires switching from `exec --json` to the app-server JSON-RPC protocol. This is a large architectural change:

- New transport: bidirectional JSON-RPC 2.0 over stdio
- New handshake: `initialize` → `initialized` → `thread/start` → `turn/start`
- New approval flow: `requestApproval` server-request → Telegram buttons → `serverRequest/resolved`
- Per-command elevation: `acceptWithExecpolicyAmendment`
- Per-host network approval: `networkApprovalContext`

**Scope:** This is essentially a new runner, not a modification of the existing one. Estimated at 800+ lines. Should only be pursued if there's strong demand for per-command Codex approval from Telegram.

### C3. Dynamic Permission Mode Switching

If/when Claude fixes the `control_request` protocol (SDK #469), enable runtime switching:
- `/tools claude mode plan` → sets `--permission-mode plan` and activates the interactive prompt infrastructure from the current branch
- `ExitPlanMode` control_request → Telegram buttons → approve/reject
- `ControlCanUseToolRequest` → Telegram buttons → allow/deny/always

This reactivates the code already written on this branch. Gate it behind a config flag (`control_protocol = true`) until the CLI bug is fixed.

---

## Resume + Changed Settings

Both runners spawn a **new subprocess** for every message. When a user changes settings via `/tools` between messages, the next run picks them up immediately while preserving conversation context:

- **Claude:** `claude -p --resume <session_id> --allowedTools X,Y,Z` — the `--resume` flag restores conversation context from Claude's session storage, but `--allowedTools` and `--permission-mode` are applied fresh from the new process args.

- **Codex:** `codex [-c sandbox_mode=...] exec --json resume <thread_id> -` — same pattern. The `-c` flags are process-level. A new process with new flags resumes the thread with new sandbox settings.

This means `/tools claude add WebSearch` takes effect on the very next message in the same conversation. No restart needed.

---

## What Happens to the Current Branch

The `feat/interactive-prompts` branch has substantial infrastructure for the `control_request` protocol:
- `StreamControlRequest` handling in `runners/claude.py`
- Prompt bridge in `runner_bridge.py` (`pending_prompts`, `prompt_responses`, `prompt_responders`, `_wait_prompt_response`)
- `encode_control_response` in `schemas/claude.py`
- Telegram interactive handler in `telegram/bridge.py` (`handle_callback_prompt`, `prompt_markup`, `format_prompt_text`)
- `keep_stdin_open`, `set_stdin_stream`, `flush_stdin_writes` in `runner.py`
- `--input-format stream-json` stdin message protocol in `runners/claude.py`
- 787 lines of tests across 3 files (`test_claude_runner.py`, `test_exec_bridge.py`, `test_prompt_bridge.py`)

**Recommendation: keep all of this code.** The implementation is correct and well-tested — it's the Claude CLI that has the bug ([SDK #469](https://github.com/anthropics/claude-agent-sdk-python/issues/469)). Our code already handles:
- `ControlInitializeRequest` / `ControlSetPermissionModeRequest` auto-responses
- `ControlCanUseToolRequest` → prompt ActionEvent → Telegram buttons → `control_response`
- stdin locking, batched writes, proper cleanup on cancel/timeout
- 300s timeout with message editing ("timed out" / "cancelled")

**Gating strategy:** Add a `control_protocol` config flag to `[claude]`:

```toml
[claude]
control_protocol = false   # default: use -p mode (reliable today)
# control_protocol = true  # enable when Claude fixes SDK #469
```

When `control_protocol = false` (default):
- Uses `-p` flag (current reliable behavior)
- `--allowedTools` for pre-run tool approval
- `permission_denials` in result event for post-run visibility (Phase A)
- A6 makes `keep_stdin_open` return `False`, reverting to base-class stdin behavior (close after payload)

When `control_protocol = true`:
- Removes `-p`, uses `--input-format stream-json` + `--permission-prompt-tool stdio`
- Keeps stdin open for bidirectional control protocol
- `StreamControlRequest` events → Telegram inline buttons → `StreamControlResponse`
- Interactive tool approval, `AskUserQuestion`, and (if Claude fixes it) `ExitPlanMode`

The flag selects between two code paths in `_build_args` and `keep_stdin_open`. All the existing branch code stays intact, just dormant behind the flag. When Anthropic ships the fix:
1. User sets `control_protocol = true`
2. Interactive prompts activate immediately
3. No code changes needed in takopi

---

## Implementation Order

### Phase A (target: this PR)
1. **A1** — `permission_denials` schema field + warning events (~30 lines)
2. **A2** — `permission_mode` config for Claude (~10 lines)
3. **A3** — Codex `sandbox_mode` / `web_search` / `network_access` config (~30 lines)
4. **A4** — `/tools` command + `EngineOverrides` extension + persistence (~250 lines)
5. **A5** — MCP server names in `/tools` output (~20 lines)
6. **A6** — `control_protocol` gate flag for existing branch code (~20 lines)
7. Tests for all of the above (~200 lines)

**Total Phase A:** ~560 lines

### Phase B (follow-up PR)
1. **B1** — Denial notification messages with inline **[Add to allowed]** buttons (~150 lines)
2. **B2** — Denial summary in completed message (~30 lines)
3. Tests (~100 lines)

**Total Phase B:** ~280 lines

### Phase C (future)
1. **C1** — System-prompt plan mode via `--append-system-prompt` (~100 lines)
2. **C2** — Codex app-server mode for interactive approval (~800 lines, separate feature)
3. **C3** — Flip `control_protocol = true` as default when SDK #469 is resolved

---

## Design Decisions (resolved)

1. **Persistence model for allowed_tools: replacement, not union.**
   The `/tools` UX supports additive commands (`add`, `remove`), but at the persistence layer the override stores the **full effective list**. When the user runs `/tools claude add WebSearch`, the handler reads the current effective list, appends `WebSearch`, and writes the complete new list to `EngineOverrides.allowed_tools`. This avoids ambiguity around removals and stays consistent with the existing override model where fields are simple replacements with topic-over-chat precedence.

2. **Override scope: per-topic + per-chat, matching existing pattern.**
   `/tools` overrides follow the same scoping as `/model` and `/reasoning`: stored at topic scope when a topic key exists, falling back to chat scope otherwise. Uses `apply_engine_override()` from `commands/overrides.py` which already handles this selection. No new exception in the override model.

3. **Admin restriction: mutating subcommands only.**
   `/tools` (show) and `/tools claude` (show) are readable by anyone. Mutating subcommands (`add`, `remove`, `set`, `mode`, `sandbox`, `search`, `network`) require `require_admin_or_private`, matching `/model` and `/reasoning`.

4. **Claude default allowed_tools: restore master default.**
   Master has `DEFAULT_ALLOWED_TOOLS = ["Bash", "Read", "Edit", "Write"]`, matching all documentation. Our branch reduced this to `["Bash", "Read"]` during the interactive-prompts work (when the control protocol was expected to handle approvals interactively). Since we're gating the control protocol behind a flag and keeping `-p` mode as default, **restore the 4-tool default** from master. For `/tools` display, show the **effective list** — whatever the runner will actually pass as `--allowedTools` — and label its source (config, override, or code default).

5. **Codex sandbox default: inherit, not forced.**
   Takopi does not inject a sandbox mode — the user's `~/.codex/config.toml` and Codex profile settings are respected. `/tools codex` shows the current state as "inherited from codex config" when no override is set. Users opt into `workspace-write` explicitly via `/tools codex sandbox write` when they want write-capable runs from Telegram. This avoids silently overriding stricter existing setups.
