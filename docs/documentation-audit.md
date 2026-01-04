# Takopi Codebase & Documentation Audit

**Date:** 2026-01-04
**Codebase Version:** 0.8.0.dev0
**Specification Version:** 0.5.0

## Codebase Overview

**Takopi** is a Telegram bot bridge for AI agent CLIs (Codex, Claude Code, OpenCode, Pi). It's a well-architected Python 3.14+ project using modern tooling (uv, anyio, msgspec, structlog).

| Metric | Value |
|--------|-------|
| Version | 0.8.0.dev0 |
| Source Files | 32 Python files |
| Test Files | 24 Python files |
| Documentation | 17+ Markdown files |
| Architecture | Plugin-based runner system with normalized event model |

---

## Documentation Issues Found

### 1. specification.md — Critical Issues

The specification is **v0.5.0** but the implementation is **v0.8.0.dev0**. Major discrepancies:

| Issue | Location | Problem |
|-------|----------|---------|
| Missing ActionKind | §4.4, lines 174-176 | `"subagent"` exists in `model.py:10-20` but not in spec |
| Incomplete Runner Protocol | §5.1, lines 191-199 | Missing `is_resume_line()`, `format_resume()`, `extract_resume()` methods |
| Lock timing violation | §5.2, lines 217-222 | Spec says acquire lock "before emitting" started event, but `runner.py:113-115` acquires then yields |
| Missing thread coordination | §6.2 | `scheduler.py` has `_busy_until` mechanism not described |
| Reply-to-progress feature | §6 | Users can reply to in-progress messages — completely undocumented |
| Pi token format | §3.1 | Pi uses filesystem paths with quoting — not mentioned |
| Config schema | §8 | No formal TOML schema documented |

**Recommendation**: Bump specification to v0.8.0 and address all discrepancies.

---

### 2. readme.md — Missing Config Options

| Missing Option | Section | Details |
|----------------|---------|---------|
| `[opencode]` section | After line 67 | Entire section missing. Should document `model` option |
| `[codex].extra_args` | Lines 57-59 | Undocumented. Default: `["-c", "notify=[]"]` |
| `[pi].cmd` | Lines 69-71 | Override pi binary name |
| `[pi].extra_args` | Lines 69-71 | Additional CLI arguments |
| `[pi].session_dir` | Lines 69-71 | Override session directory |
| `[pi].session_title` | Lines 69-71 | Set session title |
| `--debug` CLI flag | Line 100 area | Exists in `cli.py:336-339` but not documented |

---

### 3. developing.md — Missing Modules

These modules exist but aren't documented in the Module Responsibilities section:

| Module | Purpose |
|--------|---------|
| `router.py` | `AutoRouter` class for engine/resume token routing |
| `scheduler.py` | `ThreadScheduler` for per-thread FIFO scheduling |
| `events.py` | `EventFactory` helper for creating takopi events |
| `lockfile.py` | Prevents concurrent runs with same config |
| `backends_helpers.py` | `install_issue()` helper for setup guidance |
| `utils/paths.py` | Path relativization helpers |
| `utils/streams.py` | Async stream handling (`iter_bytes_lines`, `drain_stderr`) |
| `utils/subprocess.py` | Process management utilities |
| `schemas/*.py` | All 4 schema files (claude, codex, opencode, pi) |
| `runners/opencode.py` | OpenCode runner (missing from runner list) |

**Additional issues:**
- Line 26: Makefile command shown incorrectly (missing `ruff format --check`)
- Lines 174-177: Data flow shows `runner_for()` but implementation uses `entry_for()`/`entry_for_engine()`
- Lines 38-45: Missing many bridge.py components (`RunningTask`, `ProgressMessageState`, etc.)

---

### 4. Runner Documentation — Discrepancies

#### Claude Runner (`docs/runner/claude/`)

| Issue | Files | Problem |
|-------|-------|---------|
| Missing `permission_denials` | `claude-runner.md:252-256` vs `schemas/claude.py:89-102` | Documented but `StreamResultMessage` schema lacks field |
| Undocumented `subagent` kind | `runners/claude.py:117` | Used for Task/Agent tools but not in docs |

#### OpenCode Runner (`docs/runner/opencode/`)

| Issue | Files | Problem |
|-------|-------|---------|
| Status handling mismatch | `opencode-takopi-events.md:19-21` | Docs say pending/running states not emitted, but `runners/opencode.py:214-276` handles them |

---

### 5. Completely Undocumented Features

#### Environment Variables (not in README)

| Variable | Purpose |
|----------|---------|
| `TAKOPI_NO_INTERACTIVE` | Disable interactive mode (CI/non-TTY) |
| `PI_CODING_AGENT_DIR` | Override Pi session directory base |
| `TAKOPI_TRACE_PIPELINE` | Log pipeline events at info level |

#### Logging Features

- Automatic token redaction (Telegram tokens masked in all logs)
- `SafeWriter` class for graceful pipe handling
- Context-aware logging (`bind_run_context()`, `clear_context()`)

#### Lockfile System

- Lock files at `~/.takopi/takopi.lock`
- Contains JSON with PID and token fingerprint
- Auto-replaces stale locks (dead PID or different token)

---

## Recommended Fixes

### High Priority

1. **Update specification.md to v0.8.0**
   - Add `"subagent"` to ActionKind list
   - Add missing Runner Protocol methods
   - Document thread coordination mechanism
   - Add formal config TOML schema
   - Document reply-to-progress feature

2. **Update readme.md config section**
   - Add `[opencode]` section with `model` option
   - Document `extra_args` for codex and pi
   - Document all pi-specific options
   - Add `--debug` CLI flag

3. **Fix Claude schema**
   - Add `permission_denials` field to `StreamResultMessage` in `schemas/claude.py`

### Medium Priority

4. **Update developing.md**
   - Add all missing modules to Module Responsibilities
   - Fix data flow diagram method names
   - Add missing bridge.py components
   - Fix Makefile command representation

5. **Document environment variables in README**
   - Add section for all `TAKOPI_*` variables
   - Document `PI_CODING_AGENT_DIR`

6. **Document lockfile behavior**
   - Add section explaining single-instance enforcement
   - Document lock file location and format

### Low Priority

7. **Add subagent ActionKind to docs**
   - Update specification and runner docs

8. **Update OpenCode docs**
   - Clarify that implementation handles pending/running states defensively

---

## Summary Statistics

| Category | Count |
|----------|-------|
| Critical spec discrepancies | 7 |
| Missing README config options | 6 |
| Missing developing.md modules | 11 |
| Undocumented env variables | 3 |
| Runner doc discrepancies | 3 |
