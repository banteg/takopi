# Claude Code Runner Implementation Guide

This document provides a practical step-by-step guide for implementing the Claude Code
runner for Takopi. Read this alongside `specification.md` for the complete picture.

## Quick Start

### 1. Prerequisites

Ensure you have:
- Claude Code CLI installed (`npm install -g @anthropic-ai/claude-code` or similar)
- `ANTHROPIC_API_KEY` environment variable set
- Understanding of the Takopi runner protocol (see `docs/specification.md`)

### 2. Files to Create/Modify

```
src/takopi/runners/claude_code.py    # New file - main implementation
src/takopi/engines.py                 # Add engine backend registration
tests/test_claude_code_runner.py      # New file - tests
```

## Implementation Walkthrough

### Step 1: Create the Runner Class

```python
# src/takopi/runners/claude_code.py

import json
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from weakref import WeakValueDictionary

import anyio

from takopi.model import (
    Action,
    ActionEvent,
    CompletedEvent,
    EngineId,
    ResumeToken,
    StartedEvent,
    TakopiEvent,
)
from takopi.runner import ResumeRunnerMixin, compile_resume_pattern


@dataclass
class ClaudeCodeRunner(ResumeRunnerMixin):
    """Runner for Claude Code CLI."""

    engine: EngineId = "claude-code"
    resume_re: re.Pattern[str] = compile_resume_pattern("claude-code")

    claude_cmd: str = "claude"
    model: str | None = None
    extra_args: list[str] | None = None
    session_title: str = "Claude Code"

    def __post_init__(self) -> None:
        self._session_locks: WeakValueDictionary[str, anyio.Lock] = WeakValueDictionary()
        if self.extra_args is None:
            self.extra_args = []
```

### Step 2: Implement Lock Management

```python
    def _lock_for(self, token: ResumeToken) -> anyio.Lock:
        """Get or create a lock for a session."""
        key = f"{token.engine}:{token.value}"
        lock = self._session_locks.get(key)
        if lock is None:
            lock = anyio.Lock()
            self._session_locks[key] = lock
        return lock
```

### Step 3: Implement the Run Method

```python
    async def run(
        self,
        prompt: str,
        resume: ResumeToken | None,
    ) -> AsyncIterator[TakopiEvent]:
        """Execute a Claude Code session."""
        if resume is not None:
            if resume.engine != "claude-code":
                raise RuntimeError(f"Wrong engine: {resume.engine!r}")
            lock = self._lock_for(resume)
            async with lock:
                async for evt in self._run_impl(prompt, resume):
                    yield evt
        else:
            # For new sessions, lock is acquired in _run_impl
            # AFTER session_id is known
            async for evt in self._run_impl(prompt, None):
                yield evt
```

### Step 4: Build CLI Arguments

```python
    def _build_args(self, prompt: str, resume: ResumeToken | None) -> list[str]:
        """Build command line arguments for Claude Code."""
        args = [
            "--print",
            "--output-format", "stream-json",
            "--verbose",
        ]

        if resume is not None:
            args.extend(["--resume", resume.value])

        if self.model:
            args.extend(["--model", self.model])

        args.extend(self.extra_args or [])
        args.append("--")
        args.append(prompt)

        return args
```

### Step 5: Main Run Implementation

This is the critical path. Key points:
- Acquire lock AFTER session_id is known for new sessions
- Track tool_use → tool_result correlation
- Always emit exactly one CompletedEvent at the end

```python
    async def _run_impl(
        self,
        prompt: str,
        resume: ResumeToken | None,
    ) -> AsyncIterator[TakopiEvent]:
        """Internal run implementation."""
        args = self._build_args(prompt, resume)

        # Track state
        session_id: str | None = None
        session_lock: anyio.Lock | None = None
        session_lock_acquired = False
        tool_actions: dict[str, Action] = {}  # tool_use_id -> Action
        final_answer = ""
        completed_emitted = False

        proc = await anyio.open_process(
            [self.claude_cmd, *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        try:
            async for line in proc.stdout:
                line = line.decode().strip()
                if not line:
                    continue

                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue

                async for takopi_evt in self._translate_event(event, tool_actions):
                    # Handle lock acquisition for new sessions
                    if isinstance(takopi_evt, StartedEvent) and resume is None:
                        session_id = takopi_evt.resume.value
                        session_lock = self._lock_for(takopi_evt.resume)
                        await session_lock.acquire()
                        session_lock_acquired = True

                    # Track final answer
                    if isinstance(takopi_evt, CompletedEvent):
                        final_answer = takopi_evt.answer
                        completed_emitted = True

                    yield takopi_evt

            # Wait for process to exit
            await proc.wait()

            # If no completed event was emitted, emit one now
            if not completed_emitted:
                yield CompletedEvent(
                    engine="claude-code",
                    ok=proc.returncode == 0,
                    answer=final_answer,
                    resume=ResumeToken("claude-code", session_id) if session_id else None,
                    error=f"Process exited with code {proc.returncode}" if proc.returncode != 0 else None,
                )

        finally:
            if session_lock_acquired and session_lock is not None:
                session_lock.release()

            # Ensure process is terminated
            if proc.returncode is None:
                proc.terminate()
                with anyio.move_on_after(2):
                    await proc.wait()
                if proc.returncode is None:
                    proc.kill()
```

### Step 6: Event Translation

```python
    async def _translate_event(
        self,
        event: dict,
        tool_actions: dict[str, Action],
    ) -> AsyncIterator[TakopiEvent]:
        """Translate Claude Code event to Takopi events."""

        if event["type"] == "system" and event.get("subtype") == "init":
            yield StartedEvent(
                engine="claude-code",
                resume=ResumeToken("claude-code", event["session_id"]),
                title=self.session_title,
                meta={
                    "model": event.get("model"),
                    "tools": event.get("tools"),
                    "cwd": event.get("cwd"),
                },
            )

        elif event["type"] == "assistant":
            message = event.get("message", {})
            for content in message.get("content", []):
                if content["type"] == "tool_use":
                    action = self._create_action(content)
                    tool_actions[content["id"]] = action
                    yield ActionEvent(
                        engine="claude-code",
                        action=action,
                        phase="started",
                    )

        elif event["type"] == "user":
            message = event.get("message", {})
            for content in message.get("content", []):
                if content["type"] == "tool_result":
                    tool_id = content["tool_use_id"]
                    action = tool_actions.get(tool_id)
                    if action:
                        is_error = content.get("is_error", False)
                        result_content = content.get("content", "")
                        if isinstance(result_content, list):
                            # Handle array format from Task tool
                            result_content = "\n".join(
                                c.get("text", "") for c in result_content
                                if c.get("type") == "text"
                            )

                        yield ActionEvent(
                            engine="claude-code",
                            action=Action(
                                id=action.id,
                                kind=action.kind,
                                title=action.title,
                                detail={**action.detail, "result": result_content[:500]},
                            ),
                            phase="completed",
                            ok=not is_error and not self._looks_like_error(result_content),
                        )

        elif event["type"] == "result":
            yield CompletedEvent(
                engine="claude-code",
                ok=not event.get("is_error", False),
                answer=event.get("result", ""),
                resume=ResumeToken("claude-code", event["session_id"]),
                error=event.get("error"),
                usage={
                    "cost_usd": event.get("total_cost_usd"),
                    "duration_ms": event.get("duration_ms"),
                    "num_turns": event.get("num_turns"),
                    "input_tokens": event.get("usage", {}).get("input_tokens"),
                    "output_tokens": event.get("usage", {}).get("output_tokens"),
                },
            )

            # Handle permission denials
            for denial in event.get("permission_denials", []):
                yield ActionEvent(
                    engine="claude-code",
                    action=Action(
                        id=denial.get("tool_use_id", f"denied_{id(denial)}"),
                        kind="warning",
                        title=f"denied: {denial['tool_name']}",
                        detail=denial,
                    ),
                    phase="completed",
                    ok=False,
                    level="warning",
                )

    def _create_action(self, content: dict) -> Action:
        """Create an Action from a tool_use content block."""
        tool_name = content["name"]
        tool_input = content.get("input", {})

        kind, title = self._tool_to_kind_and_title(tool_name, tool_input)

        return Action(
            id=content["id"],
            kind=kind,
            title=title,
            detail={"tool": tool_name, "input": tool_input},
        )

    def _tool_to_kind_and_title(self, name: str, input_: dict) -> tuple[str, str]:
        """Map tool name to action kind and generate title."""
        match name:
            case "Bash":
                cmd = input_.get("command", "")
                return "command", cmd[:60] + ("..." if len(cmd) > 60 else "")
            case "Read":
                path = input_.get("file_path", "")
                return "tool", f"read: {path.split('/')[-1]}"
            case "Write":
                path = input_.get("file_path", "")
                return "file_change", f"write: {path.split('/')[-1]}"
            case "Edit":
                path = input_.get("file_path", "")
                return "file_change", f"edit: {path.split('/')[-1]}"
            case "Glob":
                pattern = input_.get("pattern", "")
                return "tool", f"glob: {pattern}"
            case "Grep":
                pattern = input_.get("pattern", "")
                return "tool", f"grep: {pattern[:30]}"
            case "WebSearch":
                query = input_.get("query", "")
                return "web_search", f"search: {query[:40]}"
            case "WebFetch":
                url = input_.get("url", "")
                return "tool", f"fetch: {url[:40]}"
            case "Task":
                desc = input_.get("description", "task")
                return "tool", f"task: {desc}"
            case "TodoWrite":
                return "note", "updating todos"
            case _:
                return "tool", f"tool: {name}"

    def _looks_like_error(self, content: str) -> bool:
        """Heuristic to detect error results."""
        if not content:
            return False
        lower = content.lower()
        return any(
            marker in lower
            for marker in ["error:", "failed:", "exception:", "traceback"]
        )
```

### Step 7: Register the Engine Backend

```python
# In src/takopi/engines.py, add:

import shutil
import os

def _claude_code_check_setup(config: EngineConfig, config_path: Path) -> list[SetupIssue]:
    issues = []

    claude_path = shutil.which("claude")
    if claude_path is None:
        issues.append(SetupIssue(
            "Claude Code CLI not found",
            (
                "Install via: npm install -g @anthropic-ai/claude-code",
                "Or: pip install claude-code",
            )
        ))

    if not os.environ.get("ANTHROPIC_API_KEY"):
        issues.append(SetupIssue(
            "ANTHROPIC_API_KEY not set",
            ("Set via: export ANTHROPIC_API_KEY=sk-...",)
        ))

    return issues


def _claude_code_build_runner(config: EngineConfig, config_path: Path) -> Runner:
    from takopi.runners.claude_code import ClaudeCodeRunner

    claude_cmd = shutil.which("claude")
    if not claude_cmd:
        raise ConfigError("claude not found on PATH")

    return ClaudeCodeRunner(
        claude_cmd=claude_cmd,
        model=config.get("model"),
        extra_args=config.get("extra_args", []),
    )


def _claude_code_startup_message(cwd: str) -> str:
    return f"Claude Code is ready\npwd: {cwd}"


_ENGINE_BACKENDS["claude-code"] = EngineBackend(
    id="claude-code",
    display_name="Claude Code",
    check_setup=_claude_code_check_setup,
    build_runner=_claude_code_build_runner,
    startup_message=_claude_code_startup_message,
)
```

## Testing Strategy

### Unit Tests

```python
# tests/test_claude_code_runner.py

import pytest
from takopi.runners.claude_code import ClaudeCodeRunner
from takopi.model import StartedEvent, ActionEvent, CompletedEvent, ResumeToken


def test_tool_to_kind_and_title():
    runner = ClaudeCodeRunner()

    kind, title = runner._tool_to_kind_and_title("Bash", {"command": "ls -la"})
    assert kind == "command"
    assert title == "ls -la"

    kind, title = runner._tool_to_kind_and_title("Read", {"file_path": "/foo/bar.py"})
    assert kind == "tool"
    assert title == "read: bar.py"

    kind, title = runner._tool_to_kind_and_title("WebSearch", {"query": "python asyncio"})
    assert kind == "web_search"
    assert title == "search: python asyncio"


def test_resume_format():
    runner = ClaudeCodeRunner()
    token = ResumeToken(engine="claude-code", value="abc123")

    result = runner.format_resume(token)
    assert result == "`claude-code resume abc123`"


def test_resume_extract():
    runner = ClaudeCodeRunner()

    text = "Here is your result\n`claude-code resume abc123`"
    token = runner.extract_resume(text)

    assert token is not None
    assert token.engine == "claude-code"
    assert token.value == "abc123"


def test_resume_extract_returns_none_for_other_engines():
    runner = ClaudeCodeRunner()
    text = "`codex resume abc123`"

    token = runner.extract_resume(text)
    assert token is None
```

### Integration Tests with Fixtures

```python
import json
from pathlib import Path


@pytest.fixture
def simple_fixture():
    fixture_path = Path(__file__).parent / "fixtures" / "claude_code_simple.jsonl"
    with open(fixture_path) as f:
        return [json.loads(line) for line in f if line.strip()]


def test_translate_simple_session(simple_fixture):
    runner = ClaudeCodeRunner()
    tool_actions = {}
    all_events = []

    for raw_event in simple_fixture:
        for evt in runner._translate_event(raw_event, tool_actions):
            all_events.append(evt)

    # Verify event sequence
    assert isinstance(all_events[0], StartedEvent)
    assert all_events[0].resume.value == "01941f2a-3b4c-7d8e-9f0a-1b2c3d4e5f6a"

    # Find action events
    action_events = [e for e in all_events if isinstance(e, ActionEvent)]
    assert len(action_events) == 2  # started + completed

    # Verify completed event
    assert isinstance(all_events[-1], CompletedEvent)
    assert all_events[-1].ok is True
```

### Contract Tests (Must Pass)

```python
@pytest.mark.anyio
async def test_runner_contract_started_first():
    """StartedEvent must be the first event."""
    # Use mock or ScriptRunner


@pytest.mark.anyio
async def test_runner_contract_completed_last():
    """CompletedEvent must be the last event."""


@pytest.mark.anyio
async def test_runner_contract_resume_matches():
    """CompletedEvent.resume must match StartedEvent.resume."""


@pytest.mark.anyio
async def test_runner_serializes_same_session():
    """Concurrent runs to same session must serialize."""
```

## Common Pitfalls

### 1. Lock Acquisition Timing

**Wrong:**
```python
# DON'T acquire lock before session_id is known
lock = self._lock_for(ResumeToken("claude-code", "unknown"))
async with lock:
    async for evt in self._run_impl(prompt, None):
        yield evt
```

**Right:**
```python
# Acquire lock AFTER session_id is known, BEFORE yielding started
if isinstance(takopi_evt, StartedEvent) and resume is None:
    session_lock = self._lock_for(takopi_evt.resume)
    await session_lock.acquire()  # THEN yield
    yield takopi_evt
```

### 2. Tool Result Correlation

Claude Code emits tool_use in assistant messages and tool_result in user messages
with matching IDs. You MUST track these:

```python
# Track tool_use events
if content["type"] == "tool_use":
    tool_actions[content["id"]] = action

# Later, correlate tool_result
if content["type"] == "tool_result":
    action = tool_actions.get(content["tool_use_id"])
```

### 3. Array vs String Content

Tool results can have string or array content:

```python
result_content = content.get("content", "")
if isinstance(result_content, list):
    result_content = "\n".join(
        c.get("text", "") for c in result_content
        if c.get("type") == "text"
    )
```

### 4. Always Emit CompletedEvent

Even if the process crashes, you MUST emit a CompletedEvent:

```python
finally:
    if not completed_emitted:
        yield CompletedEvent(
            engine="claude-code",
            ok=False,
            answer="",
            error="Process terminated unexpectedly",
        )
```

## Debugging Tips

1. **Enable verbose logging:**
   ```python
   import logging
   logging.getLogger("takopi.runners.claude_code").setLevel(logging.DEBUG)
   ```

2. **Test with fixtures first:**
   ```bash
   pytest tests/test_claude_code_runner.py -v
   ```

3. **Test CLI invocation manually:**
   ```bash
   claude --print --output-format stream-json --verbose -- "Say hello"
   ```

4. **Check for process leaks:**
   ```bash
   ps aux | grep claude
   ```

## Configuration Reference

```toml
# takopi.toml

[claude-code]
model = "sonnet"                          # Optional: opus, sonnet, haiku
extra_args = ["--max-turns", "5"]         # Optional: additional CLI args
```

## Checklist Before PR

- [ ] All contract tests pass
- [ ] Event translation tests pass
- [ ] Serialization tests pass
- [ ] Resume formatting/extraction tests pass
- [ ] Engine registration works
- [ ] CLI flag `--engine claude-code` works
- [ ] Progress rendering looks correct in Telegram
- [ ] Cancellation via `/cancel` works
- [ ] Error handling tested (API errors, process crashes)
- [ ] No process leaks after cancellation
