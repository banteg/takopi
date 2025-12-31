# Spec Alignment Checklist

Checklist for aligning takopi codebase with Specification v0.2.0.

Legend:
- 🔴 Breaking change
- 🟡 New requirement  
- 🟢 Refactor only
- ⚠️ Needs decision

---

## Phase 1: Domain Model (§5)

### 1. Create `model.py` with domain types
🟢 Extract from `runners/base.py` → new `takopi/model.py`

- [x] 1.1 Move `ResumeToken` to `model.py`
- [x] 1.2 Move `RunResult` to `model.py`
- [x] 1.3 Move `Action` TypedDict to `model.py`
- [x] 1.4 Move all `TakopiEvent` types to `model.py`
- [x] 1.5 Move `ActionKind` type alias to `model.py`
- [x] 1.6 Move `TakopiEventType` type alias to `model.py`
- [x] 1.7 Add re-exports in `runners/base.py` for backwards compatibility

### 2. Update `EngineId` typing (§11.2)
🟢 Loosen type from closed Literal to open string

- [x] 2.1 Change `EngineId: TypeAlias = Literal["codex", "mock"]` → `EngineId = NewType("EngineId", str)`
- [x] 2.2 Update all type annotations that use `EngineId`

### 3. Add required `title` field to `session.started` (§5.3.1)
🟡 New required field

- [x] 3.1 Update `SessionStartedEvent` TypedDict to include `title: str`
- [x] 3.2 Update `CodexRunner` to emit `title` (use profile name or "Codex")
- [x] 3.3 Update `MockRunner` to emit `title`
- [x] 3.4 Update `ScriptRunner` to emit `title`
- [x] 3.5 Update renderer to display session title if desired

### 4. Make `ok` required on `action.completed` (§5.3.3)
🔴 Schema tightening

- [x] 4.1 Update `ActionCompletedEvent` to require `ok: bool` at top level (not inside Action)
- [x] 4.2 Update `CodexRunner._translate_item_event()` to always compute `ok` (default `True` if unknown)
- [x] 4.3 Update `MockRunner` event emission
- [x] 4.4 Update renderer to read `ok` from event, not from `action.detail`

### 5. Add optional `detail` to `error` event (§5.3.5)
🟢 Already compatible, document behavior

- [x] 5.1 Update `ErrorEvent` TypedDict to include `detail: str` (optional)
- [x] 5.2 Update `CodexRunner` to populate `detail` with stderr tail on crash

---

## Phase 2: Runner Protocol (§6)

### 6. Make `on_event` parameter non-optional (§6.1)
🔴 Signature change

- [x] 6.1 Update `Runner` Protocol: `on_event: EventSink` (remove `| None`)
- [x] 6.2 Update `CodexRunner.run()` signature
- [x] 6.3 Update `MockRunner.run()` signature
- [x] 6.4 Update `ScriptRunner.run()` signature
- [x] 6.5 Create `NO_OP_SINK: EventSink = lambda _: None` helper for tests
- [x] 6.6 Update all test call sites to pass `on_event=NO_OP_SINK` or a real sink

### 7. Implement pre-emit locking for new sessions (§6.2)
🔴 Critical behavioral change

- [x] 7.1 In `CodexRunner._run()`: parse `thread_id` from stream
- [x] 7.2 Acquire lock for new token **before** emitting `session.started`
- [x] 7.3 Hold lock for remainder of run
- [x] 7.4 Add test: two concurrent `resume=None` runs that get same thread_id must serialize
- [x] 7.5 Add test: verify `session.started` not emitted until lock acquired

### 8. Callback errors must abort run (§6.4)
🔴 Behavioral change

- [x] 8.1 Update `EventQueue._drain()`: re-raise exceptions instead of logging and continuing
- [x] 8.2 Ensure runner catches the exception and terminates subprocess
- [x] 8.3 Add test: callback that raises → run aborts with error status
- [x] 8.4 Document migration: callbacks must not raise (or run fails)

---

## Phase 3: Module Restructuring (§3.2)

### 9. Create `runner.py` with protocol and utilities
🟢 File rename/reorganize

- [x] 9.1 Create `takopi/runner.py`
- [x] 9.2 Move `Runner` Protocol from `runners/base.py` → `runner.py`
- [x] 9.3 Move `EventQueue` from `runners/base.py` → `runner.py`
- [x] 9.4 Move `EventSink` type alias → `runner.py`
- [x] 9.5 Remove re-export shim (`runners/base.py`)

### 10. Create `render.py` from `exec_render.py`
🟢 File rename

- [x] 10.1 Rename `exec_render.py` → `render.py`
- [x] 10.2 Update all imports

### 11. Create `bridge.py` from `exec_bridge.py` orchestration logic
🟢 Extract and rename

- [x] 11.1 Create `takopi/bridge.py`
- [x] 11.2 Move `BridgeConfig` → `bridge.py`
- [x] 11.3 Move `ProgressEdits` → `bridge.py`
- [x] 11.4 Move `handle_message()` → `bridge.py`
- [x] 11.5 Move `poll_updates()` → `bridge.py`
- [x] 11.6 Move `_run_main_loop()` → `bridge.py`
- [x] 11.7 Move cancel/resume helpers → `bridge.py`

### 12. Create `cli.py` with entry points
🟢 Extract from exec_bridge

- [x] 12.1 Create `takopi/cli.py`
- [x] 12.2 Move `run()` typer command → `cli.py`
- [x] 12.3 Move `main()` → `cli.py`
- [x] 12.4 Move `_version_callback()` → `cli.py`
- [x] 12.5 Move config parsing (`_parse_bridge_config`) → `cli.py`
- [x] 12.6 Update `pyproject.toml` entry point: `takopi = "takopi.cli:main"`

### 13. Create `markdown.py` for Telegram formatting
🟢 Extract from exec_bridge

- [x] 13.1 Create `takopi/markdown.py`
- [x] 13.2 Move `truncate_for_telegram()` → `markdown.py`
- [x] 13.3 Move `prepare_telegram()` → `markdown.py`
- [x] 13.4 Move `render_markdown()` from `exec_render.py` → `markdown.py`
- [x] 13.5 Move `TELEGRAM_MARKDOWN_LIMIT` constant → `markdown.py`

### 14. Delete `exec_bridge.py` after extraction
🟢 Cleanup

- [x] 14.1 Verify all code moved to `bridge.py`, `cli.py`, `markdown.py`
- [x] 14.2 Delete `exec_bridge.py`
- [x] 14.3 Update any remaining imports

---

## Phase 4: Bridge Behavior (§7)

### 15. Document SIGTERM → SIGKILL escalation (§7.4)
🟢 Documentation only (code already correct)

- [x] 15.1 Add docstring to `manage_subprocess()` explaining 2s timeout before SIGKILL
- [ ] 15.2 Update spec §7.4 to document escalation (or add §7.4.1)

### 16. Ensure `/cancel` ignores accompanying text (§7.4)
🟢 Verify existing behavior

- [x] 16.1 Add test: `/cancel some extra text` still cancels
- [x] 16.2 Verify current code uses `text == "/cancel"` or `text.startswith("/cancel")`

### 17. Add warning for unparseable resume attempts (§4.4)
🟡 New user-facing behavior

- [x] 17.1 Define heuristic for "looks like resume attempt" (e.g., contains "resume" keyword)
- [x] 17.2 If `extract_resume()` returns `None` but text looks like resume → send warning
- [x] 17.3 Add test for warning message

### 18. Crash handling: include resume line in error (§6.5)
🟡 Verify/implement

- [x] 18.1 Verify that on subprocess crash, if `session.started` was received, error message includes resume line
- [x] 18.2 Add test: runner crashes after emitting session.started → error includes resume line

---

## Phase 5: Renderer Updates (§8)

### 19. Renderer must not depend on engine-native events (§8.1)
🟢 Verify existing compliance

- [x] 19.1 Audit `ExecProgressRenderer` — confirm it only consumes `TakopiEvent`, not raw codex JSON
- [x] 19.2 Document this constraint in renderer docstring

### 20. Renderer state: add session title (§8.2)
🟡 New feature

- [x] 20.1 Add `session_title: str | None` to `ExecProgressRenderer`
- [x] 20.2 Update `note_event()` to capture title from `session.started`
- [x] 20.3 Optionally display title in progress header

---

## Phase 6: Testing Requirements (§10)

### 21. Add event factories for test readability (§10.2)
🟢 Test infrastructure

- [x] 21.1 Create `tests/factories.py`
- [x] 21.2 Add `session_started(engine, value, title)` factory
- [x] 21.3 Add `action_started(id, kind, title, detail)` factory
- [x] 21.4 Add `action_completed(id, kind, title, ok, detail)` factory
- [x] 21.5 Add `log_event(message, level)` factory
- [x] 21.6 Add `error_event(message, detail)` factory
- [x] 21.7 Refactor existing tests to use factories

### 22. Runner contract tests (§10.1.1)
🟡 New test category

- [x] 22.1 Test: runner emits exactly one `session.started`
- [x] 22.2 Test: all actions have `id`, `kind`, `title`
- [x] 22.3 Test: `RunResult.resume` matches `session.started` token
- [x] 22.4 Test: events delivered in order
- [x] 22.5 Test: `action.completed` always has `ok` field

### 23. Per-thread serialization test (§10.1.2) — critical
🟡 New test

- [x] 23.1 Test: new session blocks, second run with same token waits
- [x] 23.2 Test: first run completes, second run proceeds
- [x] 23.3 Test: parallel runs with different tokens execute concurrently

### 24. Bridge progress throttling tests (§10.1.3)
🟢 May already exist, verify coverage

- [x] 24.1 Test: edits no more frequent than `progress_edit_every`
- [x] 24.2 Test: no edit if content unchanged
- [x] 24.3 Test: truncation preserves resume line

### 25. Cancellation tests (§10.1.4)
🟢 May already exist, verify coverage

- [x] 25.1 Test: `/cancel` terminates run
- [x] 25.2 Test: cancelled status message sent
- [x] 25.3 Test: resume line included if known

### 26. Renderer formatting tests (§10.1.5)
🟢 May already exist, verify coverage

- [x] 26.1 Test: action rendering (started/completed)
- [x] 26.2 Test: error rendering
- [x] 26.3 Test: log rendering
- [x] 26.4 Test: stable output under repeated event sequences

---

## Phase 7: Configuration (§9)

### 27. Warn on cross-engine resume attempt (§9.1)
🟡 New behavior

- [x] 27.1 If resume extraction fails but message contains another engine's pattern → warn user
- [x] 27.2 Add test: message contains `claude resume <id>` with codex engine → warning

---

## Phase 8: Documentation

### 28. Update module docstrings
🟢 Documentation

- [x] 28.1 Add docstring to `model.py` explaining domain types
- [x] 28.2 Add docstring to `runner.py` explaining protocol
- [x] 28.3 Add docstring to `bridge.py` explaining orchestration
- [x] 28.4 Add docstring to `render.py` explaining purity constraints
- [x] 28.5 Add docstring to `markdown.py` explaining Telegram constraints

### 29. Update README/developing.md
🟢 Documentation

- [x] 29.1 Document new module structure
- [x] 29.2 Document how to add a new runner
- [x] 29.3 Reference spec for authoritative behavior

---

## Suggested Execution Order

**Week 1: Foundation (non-breaking)**
1. Items 1-2 (model.py, EngineId)
2. Items 9-14 (module restructuring)
3. Item 21 (event factories)

**Week 2: Schema updates (minor breaking)**
4. Items 3-5 (event schema: title, ok, detail)
5. Items 19-20 (renderer updates)
6. Items 22, 26 (contract + renderer tests)

**Week 3: Protocol changes (breaking)**
7. Items 6-8 (on_event required, locking, callback abort)
8. Items 23-25 (serialization + cancellation tests)

**Week 4: Behavior polish**
9. Items 15-18 (bridge behavior)
10. Item 27 (cross-engine warning)
11. Items 28-29 (documentation)

---

## Decision Points Needed

| Item | Question | Options |
|------|----------|---------|
| 3.2 | Where does session `title` come from? | Profile name / Model name / Config / Hardcode "Codex" |
| 4.2 | Default `ok` value when unknown? | `True` / `None` (violates spec) |
| 8.1 | Callback abort: immediate or next event? | Immediate re-raise / Set flag and abort after drain |
| 17.1 | Heuristic for "looks like resume"? | Contains "resume" keyword / Regex for any `<word> resume <id>` pattern |

---

## Summary

| Phase | Items | Breaking | Effort |
|-------|-------|----------|--------|
| 1. Domain Model | 1-5 | Minor | Low |
| 2. Runner Protocol | 6-8 | **Yes** | Medium |
| 3. Module Restructure | 9-14 | No | Medium |
| 4. Bridge Behavior | 15-18 | Minor | Low |
| 5. Renderer | 19-20 | No | Low |
| 6. Testing | 21-26 | No | Medium |
| 7. Configuration | 27 | No | Low |
| 8. Documentation | 28-29 | No | Low |

**Total: 29 top-level items, ~85 sub-tasks**
