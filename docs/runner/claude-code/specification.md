# Claude Code Runner Specification

This document specifies the Claude Code runner implementation for Takopi, enabling the
Telegram bot to orchestrate Claude Code sessions with live progress streaming and thread
resumption.

## 1. Overview

### 1.1 Claude Code CLI

Claude Code is Anthropic's official CLI for Claude. It provides:
- Interactive coding assistance with file system access
- Tool execution (Bash, Read, Write, Edit, Grep, Glob, WebFetch, etc.)
- Session persistence and resumption via session IDs
- Streaming JSON output via `--output-format stream-json`

### 1.2 Runner Goals

1. Spawn Claude Code subprocess with streaming JSON output
2. Translate Claude Code events into Takopi normalized events
3. Support thread resumption via `--resume <session_id>`
4. Enforce per-thread serialization (same session ID = sequential execution)
5. Handle cancellation via SIGTERM/SIGINT
6. Capture and report errors gracefully

## 2. CLI Invocation

### 2.1 Command Pattern

```bash
# New session
claude --print --output-format stream-json --verbose -- "<prompt>"

# Resume session
claude --resume <session_id> --print --output-format stream-json --verbose -- "<prompt>"
```

Key flags:
- `--print`: Non-interactive mode (required for SDK usage)
- `--output-format stream-json`: Streams JSONL to stdout
- `--verbose`: Required for stream-json to emit all events
- `--resume <session_id>`: Resume an existing session
- `--`: Separator before the prompt

### 2.2 Optional Flags

```bash
--model <opus|sonnet|haiku>      # Model selection (default: sonnet)
--max-turns <n>                  # Limit agent turns
--system-prompt <text>           # Override system prompt
--append-system-prompt <text>    # Append to system prompt
--allowedTools <tool1,tool2>     # Whitelist tools
--disallowedTools <tool1,tool2>  # Blacklist tools
--add-dir <path>                 # Add directory to context
--mcp-config <path>              # MCP server configuration
```

### 2.3 Environment Variables

```bash
ANTHROPIC_API_KEY=<key>          # Required
ANTHROPIC_BASE_URL=<url>         # Optional: proxy/custom endpoint
```

## 3. Stream Event Format

Claude Code emits newline-delimited JSON (JSONL) to stdout. Each line is a complete
JSON object with a `type` field.

### 3.1 Event Types Overview

| Event Type | Description | Session ID Available |
|------------|-------------|---------------------|
| `system` (subtype: `init`) | Session initialization | Yes (first event with session_id) |
| `assistant` | Assistant message with content | Yes |
| `user` | User/tool result message | Yes |
| `result` | Final result with usage stats | Yes |

### 3.2 System Init Event

The first event emitted, containing session metadata:

```json
{
  "type": "system",
  "subtype": "init",
  "session_id": "abc123-def456-...",
  "cwd": "/path/to/working/directory",
  "model": "claude-sonnet-4-20250514",
  "permissionMode": "default",
  "apiKeySource": "environment",
  "tools": ["Bash", "Read", "Write", "Edit", "Glob", "Grep", ...],
  "mcp_servers": [{"name": "server1", "status": "connected"}]
}
```

**Critical**: The `session_id` in this event is the resume token value.

### 3.3 Assistant Message Event

Emitted when Claude produces output:

```json
{
  "type": "assistant",
  "session_id": "abc123-def456-...",
  "message": {
    "id": "msg_01ABC...",
    "type": "message",
    "role": "assistant",
    "model": "claude-sonnet-4-20250514",
    "content": [
      {"type": "text", "text": "I'll help you with that..."},
      {"type": "tool_use", "id": "toolu_01XYZ...", "name": "Bash", "input": {"command": "ls -la"}}
    ],
    "usage": {
      "input_tokens": 1500,
      "output_tokens": 200,
      "cache_read_input_tokens": 1000,
      "cache_creation_input_tokens": 0
    }
  }
}
```

### 3.4 User Message Event (Tool Results)

Emitted after tool execution:

```json
{
  "type": "user",
  "session_id": "abc123-def456-...",
  "message": {
    "id": "msg_02DEF...",
    "type": "message",
    "role": "user",
    "content": [
      {
        "type": "tool_result",
        "tool_use_id": "toolu_01XYZ...",
        "content": "file1.txt\nfile2.txt\n..."
      }
    ]
  }
}
```

### 3.5 Result Event

Final event with session summary:

```json
{
  "type": "result",
  "subtype": "success",
  "session_id": "abc123-def456-...",
  "total_cost_usd": 0.0234,
  "is_error": false,
  "duration_ms": 15234,
  "duration_api_ms": 12500,
  "num_turns": 3,
  "result": "I've completed the task. Here's a summary...",
  "usage": {
    "input_tokens": 5000,
    "output_tokens": 1200,
    "cache_read_input_tokens": 3000,
    "cache_creation_input_tokens": 500,
    "server_tool_use": {"web_search_requests": 0}
  },
  "modelUsage": {
    "claude-sonnet-4-20250514": {
      "inputTokens": 5000,
      "outputTokens": 1200,
      "costUSD": 0.0234
    }
  }
}
```

For errors:
```json
{
  "type": "result",
  "subtype": "error",
  "session_id": "abc123-def456-...",
  "is_error": true,
  "error": "Rate limit exceeded",
  "result": "",
  "permission_denials": [
    {"tool_name": "Bash", "tool_use_id": "toolu_01...", "tool_input": {"command": "rm -rf /"}}
  ]
}
```

## 4. Event Translation to Takopi Model

### 4.1 Mapping Table

| Claude Code Event | Takopi Event | Notes |
|-------------------|--------------|-------|
| `system` (init) | `StartedEvent` | Extract session_id as resume token |
| `assistant` with tool_use | `ActionEvent` (started) | One per tool_use in content |
| `user` with tool_result | `ActionEvent` (completed) | Match by tool_use_id |
| `result` (success) | `CompletedEvent` (ok=True) | Use `result` as answer |
| `result` (error) | `CompletedEvent` (ok=False) | Use `error` as error message |

### 4.2 Action Kind Mapping

| Content Type | Action Kind | Title Source |
|--------------|-------------|--------------|
| `tool_use` name="Bash" | `command` | `input.command` (truncated) |
| `tool_use` name="Read" | `tool` | `tool: Read` + path |
| `tool_use` name="Write" | `file_change` | `file: <path>` |
| `tool_use` name="Edit" | `file_change` | `edit: <path>` |
| `tool_use` name="Glob" | `tool` | `glob: <pattern>` |
| `tool_use` name="Grep" | `tool` | `grep: <pattern>` |
| `tool_use` name="WebSearch" | `web_search` | `search: <query>` |
| `tool_use` name="WebFetch" | `tool` | `fetch: <url>` |
| `tool_use` name="Task" | `tool` | `task: <description>` |
| `tool_use` name="TodoWrite" | `note` | `todo: <summary>` |
| `tool_use` name="AskUserQuestion" | `note` | `question: <summary>` |
| `text` (thinking) | `note` | `thinking...` |
| Other tool_use | `tool` | `tool: <name>` |

### 4.3 Action ID Generation

Claude Code uses `tool_use.id` (e.g., `toolu_01XYZ...`) which is stable within a session.
Use this directly as `Action.id`.

For text content blocks without IDs, generate synthetic IDs:
- `text_<message_id>_<index>` for regular text
- `thinking_<message_id>_<index>` for thinking blocks

### 4.4 Translation Algorithm

```python
def translate_claude_code_event(event: dict) -> list[TakopiEvent]:
    events = []

    if event["type"] == "system" and event.get("subtype") == "init":
        events.append(StartedEvent(
            engine="claude-code",
            resume=ResumeToken(engine="claude-code", value=event["session_id"]),
            title="Claude Code",
            meta={"model": event.get("model"), "tools": event.get("tools")}
        ))

    elif event["type"] == "assistant":
        message = event["message"]
        for content in message["content"]:
            if content["type"] == "tool_use":
                events.append(ActionEvent(
                    engine="claude-code",
                    action=Action(
                        id=content["id"],
                        kind=tool_to_kind(content["name"]),
                        title=format_tool_title(content["name"], content.get("input", {})),
                        detail={"tool": content["name"], "input": content.get("input")}
                    ),
                    phase="started"
                ))
            elif content["type"] == "text" and content.get("text"):
                # Optional: emit as note for verbose progress
                pass
            elif content["type"] == "thinking":
                # Optional: emit thinking as debug note
                pass

    elif event["type"] == "user":
        message = event["message"]
        for content in message["content"]:
            if content["type"] == "tool_result":
                tool_id = content["tool_use_id"]
                # Determine success based on content
                ok = not is_error_result(content.get("content", ""))
                events.append(ActionEvent(
                    engine="claude-code",
                    action=Action(
                        id=tool_id,
                        kind="tool",  # Will be updated by correlating with started event
                        title="",     # Filled from correlation
                        detail={"result": truncate(content.get("content", ""), 500)}
                    ),
                    phase="completed",
                    ok=ok
                ))

    elif event["type"] == "result":
        events.append(CompletedEvent(
            engine="claude-code",
            ok=not event.get("is_error", False),
            answer=event.get("result", ""),
            resume=ResumeToken(engine="claude-code", value=event["session_id"]),
            error=event.get("error"),
            usage={
                "cost_usd": event.get("total_cost_usd"),
                "duration_ms": event.get("duration_ms"),
                "num_turns": event.get("num_turns"),
                "input_tokens": event.get("usage", {}).get("input_tokens"),
                "output_tokens": event.get("usage", {}).get("output_tokens"),
            }
        ))

    return events
```

## 5. Resume Token Format

### 5.1 Token Structure

```python
ResumeToken(engine="claude-code", value="<session_id>")
```

Session IDs are UUIDs generated by Claude Code, e.g.: `01941f2a-3b4c-7d8e-9f0a-1b2c3d4e5f6a`

### 5.2 Resume Line Format

Canonical format for embedding in Telegram messages:

```
`claude-code resume <session_id>`
```

Example:
```
`claude-code resume 01941f2a-3b4c-7d8e-9f0a-1b2c3d4e5f6a`
```

### 5.3 Resume Regex

```python
RESUME_RE = re.compile(
    r"(?im)^\s*`?claude-code\s+resume\s+(?P<token>[0-9a-f-]+)`?\s*$"
)
```

## 6. Runner Implementation

### 6.1 Class Structure

```python
class ClaudeCodeRunner(ResumeRunnerMixin):
    engine: EngineId = "claude-code"
    resume_re = RESUME_RE

    def __init__(
        self,
        *,
        claude_cmd: str = "claude",
        extra_args: list[str] | None = None,
        model: str | None = None,
        title: str = "Claude Code",
    ):
        self.claude_cmd = claude_cmd
        self.extra_args = extra_args or []
        self.model = model
        self.session_title = title
        self._session_locks: WeakValueDictionary[str, anyio.Lock] = WeakValueDictionary()
```

### 6.2 Run Method (Critical Path)

```python
async def run(
    self,
    prompt: str,
    resume: ResumeToken | None,
) -> AsyncIterator[TakopiEvent]:
    if resume is not None:
        if resume.engine != "claude-code":
            raise RuntimeError(f"Wrong engine: {resume.engine!r}")
        lock = self._lock_for(resume)
        async with lock:
            async for evt in self._run(prompt, resume):
                yield evt
    else:
        # For new sessions, acquire lock AFTER session_id is known
        async for evt in self._run(prompt, None):
            yield evt
```

### 6.3 Internal Run Method

```python
async def _run(
    self,
    prompt: str,
    resume: ResumeToken | None,
) -> AsyncIterator[TakopiEvent]:
    args = self._build_args(prompt, resume)

    async with manage_subprocess(self.claude_cmd, args) as proc:
        session_id: str | None = None
        session_lock: anyio.Lock | None = None
        session_lock_acquired = False
        final_answer: str = ""
        action_map: dict[str, Action] = {}  # tool_use_id -> Action

        try:
            async for line in proc.stdout:
                if not line.strip():
                    continue

                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue

                for takopi_evt in self._translate(event, action_map):
                    # Handle lock acquisition for new sessions
                    if isinstance(takopi_evt, StartedEvent) and resume is None:
                        session_id = takopi_evt.resume.value
                        session_lock = self._lock_for(takopi_evt.resume)
                        await session_lock.acquire()
                        session_lock_acquired = True

                    # Track final answer
                    if isinstance(takopi_evt, CompletedEvent):
                        final_answer = takopi_evt.answer

                    yield takopi_evt

        finally:
            if session_lock_acquired and session_lock is not None:
                session_lock.release()
```

### 6.4 Argument Building

```python
def _build_args(self, prompt: str, resume: ResumeToken | None) -> list[str]:
    args = ["--print", "--output-format", "stream-json", "--verbose"]

    if resume is not None:
        args.extend(["--resume", resume.value])

    if self.model:
        args.extend(["--model", self.model])

    args.extend(self.extra_args)
    args.append("--")
    args.append(prompt)

    return args
```

## 7. Error Handling

### 7.1 Subprocess Errors

| Error Type | Detection | Takopi Event |
|------------|-----------|--------------|
| Process crash | Non-zero exit, no result event | `CompletedEvent(ok=False, error="Process exited with code X")` |
| Startup failure | No events emitted | `CompletedEvent(ok=False, error="Failed to start")` |
| Parse error | Invalid JSON line | Log warning, skip line |
| API error | `result.is_error=true` | `CompletedEvent(ok=False, error=result.error)` |

### 7.2 Permission Denials

When Claude Code reports permission denials in the result event, surface them:

```python
if event.get("permission_denials"):
    denials = event["permission_denials"]
    for denial in denials:
        yield ActionEvent(
            engine="claude-code",
            action=Action(
                id=denial.get("tool_use_id", f"denied_{i}"),
                kind="warning",
                title=f"permission denied: {denial['tool_name']}",
                detail=denial
            ),
            phase="completed",
            ok=False,
            level="warning"
        )
```

### 7.3 Timeout Handling

Claude Code sessions can run for extended periods. The runner should:
- NOT impose a hard timeout (let the bridge/user control this)
- Support SIGTERM for graceful shutdown
- Escalate to SIGKILL after 2-3 seconds if SIGTERM doesn't work

## 8. Configuration

### 8.1 TOML Configuration

```toml
[claude-code]
model = "sonnet"                    # Optional: opus, sonnet, haiku
extra_args = ["--max-turns", "10"]  # Optional: additional CLI args
```

### 8.2 Engine Backend Registration

```python
def _claude_code_check_setup(config: EngineConfig, config_path: Path) -> list[SetupIssue]:
    issues = []

    # Check for claude binary
    claude_path = shutil.which("claude")
    if claude_path is None:
        issues.append(SetupIssue(
            "Claude Code CLI not found",
            ("Install via: npm install -g @anthropic-ai/claude-code",)
        ))

    # Check for API key
    if not os.environ.get("ANTHROPIC_API_KEY"):
        issues.append(SetupIssue(
            "ANTHROPIC_API_KEY not set",
            ("Set via: export ANTHROPIC_API_KEY=sk-...",)
        ))

    return issues

def _claude_code_build_runner(config: EngineConfig, config_path: Path) -> Runner:
    claude_cmd = shutil.which("claude") or "claude"
    return ClaudeCodeRunner(
        claude_cmd=claude_cmd,
        model=config.get("model"),
        extra_args=config.get("extra_args", []),
    )

def _claude_code_startup_message(cwd: str) -> str:
    return f"Claude Code is ready\npwd: {cwd}"

# Registration
_ENGINE_BACKENDS["claude-code"] = EngineBackend(
    id="claude-code",
    display_name="Claude Code",
    check_setup=_claude_code_check_setup,
    build_runner=_claude_code_build_runner,
    startup_message=_claude_code_startup_message,
)
```

## 9. Testing Requirements

### 9.1 Contract Tests

```python
@pytest.mark.anyio
async def test_claude_code_runner_emits_started_first():
    """StartedEvent must be emitted before any ActionEvents."""
    runner = ClaudeCodeRunner(claude_cmd="claude")
    events = [e async for e in runner.run("test prompt", None)]

    assert len(events) >= 2
    assert isinstance(events[0], StartedEvent)
    assert events[0].engine == "claude-code"
    assert events[0].resume.engine == "claude-code"

@pytest.mark.anyio
async def test_claude_code_runner_emits_completed_last():
    """CompletedEvent must be the final event."""
    runner = ClaudeCodeRunner(claude_cmd="claude")
    events = [e async for e in runner.run("test prompt", None)]

    assert len(events) >= 2
    assert isinstance(events[-1], CompletedEvent)
    assert events[-1].engine == "claude-code"

@pytest.mark.anyio
async def test_claude_code_runner_resume_matches():
    """CompletedEvent.resume must match StartedEvent.resume."""
    runner = ClaudeCodeRunner(claude_cmd="claude")
    events = [e async for e in runner.run("test prompt", None)]

    started = next(e for e in events if isinstance(e, StartedEvent))
    completed = next(e for e in events if isinstance(e, CompletedEvent))

    assert completed.resume == started.resume
```

### 9.2 Serialization Tests

```python
@pytest.mark.anyio
async def test_claude_code_serializes_same_session():
    """Concurrent runs to same session must serialize."""
    runner = ClaudeCodeRunner(claude_cmd="claude")
    token = ResumeToken(engine="claude-code", value="test-session-123")

    order = []

    async def run_and_record(name: str):
        async for _ in runner.run(f"prompt {name}", token):
            pass
        order.append(name)

    async with anyio.create_task_group() as tg:
        tg.start_soon(run_and_record, "first")
        await anyio.sleep(0.01)  # Ensure ordering
        tg.start_soon(run_and_record, "second")

    assert order == ["first", "second"]
```

### 9.3 Event Translation Tests

```python
def test_translate_system_init():
    """System init event should produce StartedEvent."""
    event = {
        "type": "system",
        "subtype": "init",
        "session_id": "abc123",
        "model": "claude-sonnet-4",
        "tools": ["Bash", "Read"]
    }

    result = translate_claude_code_event(event)

    assert len(result) == 1
    assert isinstance(result[0], StartedEvent)
    assert result[0].resume.value == "abc123"

def test_translate_tool_use():
    """Tool use in assistant message should produce ActionEvent."""
    event = {
        "type": "assistant",
        "session_id": "abc123",
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_01XYZ",
                    "name": "Bash",
                    "input": {"command": "ls -la"}
                }
            ]
        }
    }

    result = translate_claude_code_event(event)

    assert len(result) == 1
    assert isinstance(result[0], ActionEvent)
    assert result[0].action.kind == "command"
    assert result[0].phase == "started"
```

## 10. Differences from Codex Runner

| Aspect | Codex | Claude Code |
|--------|-------|-------------|
| CLI command | `codex exec --json` | `claude --print --output-format stream-json` |
| Session identifier | `thread_id` | `session_id` |
| Event structure | `thread.*`, `turn.*`, `item.*` | `system`, `assistant`, `user`, `result` |
| Action lifecycle | `item.started` → `item.completed` | `tool_use` → `tool_result` (in separate events) |
| Resume flag | `resume <uuid> -` | `--resume <uuid>` |
| Final answer | `agent_message` item | `result.result` field |
| Usage/cost | `turn.completed.usage` | `result.usage` + `result.total_cost_usd` |

## 11. Implementation Checklist

- [ ] Create `src/takopi/runners/claude_code.py`
- [ ] Implement `ClaudeCodeRunner` class with `ResumeRunnerMixin`
- [ ] Implement `_translate()` method for event conversion
- [ ] Implement per-session locking (critical for new sessions)
- [ ] Register engine backend in `engines.py`
- [ ] Add `[claude-code]` config section support
- [ ] Create unit tests for event translation
- [ ] Create integration tests with mock subprocess
- [ ] Create contract tests for runner protocol
- [ ] Add JSONL fixtures for testing
- [ ] Update documentation

## Appendix A: Full Event Sequence Example

```
# New session initiated with prompt "List files in current directory"

→ {"type":"system","subtype":"init","session_id":"019abc...","cwd":"/home/user","model":"claude-sonnet-4","tools":["Bash","Read",...]}
→ {"type":"assistant","session_id":"019abc...","message":{"content":[{"type":"text","text":"I'll list the files..."},{"type":"tool_use","id":"toolu_01...","name":"Bash","input":{"command":"ls -la"}}]}}
→ {"type":"user","session_id":"019abc...","message":{"content":[{"type":"tool_result","tool_use_id":"toolu_01...","content":"total 32\ndrwxr-xr-x..."}]}}
→ {"type":"assistant","session_id":"019abc...","message":{"content":[{"type":"text","text":"Here are the files in your current directory:\n\n- file1.txt\n- file2.py\n..."}]}}
→ {"type":"result","subtype":"success","session_id":"019abc...","total_cost_usd":0.0012,"is_error":false,"duration_ms":2500,"num_turns":1,"result":"Here are the files..."}
```

Translated to Takopi events:
```python
StartedEvent(engine="claude-code", resume=ResumeToken("claude-code", "019abc..."), title="Claude Code")
ActionEvent(engine="claude-code", action=Action(id="toolu_01...", kind="command", title="ls -la"), phase="started")
ActionEvent(engine="claude-code", action=Action(id="toolu_01...", kind="command", title="ls -la"), phase="completed", ok=True)
CompletedEvent(engine="claude-code", ok=True, answer="Here are the files...", resume=ResumeToken("claude-code", "019abc..."))
```
