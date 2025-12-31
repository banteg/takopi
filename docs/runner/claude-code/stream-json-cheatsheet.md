# Claude Code `--output-format stream-json` event cheatsheet

`claude --print --output-format stream-json --verbose` writes **one JSON object per line**
(JSONL) to stdout. Each line is a top-level **event** with a `type` field.

Below: **all fields** for every event type plus a **full-line example** for each shape
that can be emitted.

## Top-level event types

### `system` (subtype: `init`)

First event emitted. Contains session metadata.

Fields:
- `type`: `"system"`
- `subtype`: `"init"`
- `session_id`: UUID string (the resume token)
- `cwd`: Current working directory
- `model`: Model identifier (e.g., `"claude-sonnet-4-20250514"`)
- `permissionMode`: Permission mode (e.g., `"default"`)
- `apiKeySource`: API key source (e.g., `"environment"`)
- `tools`: Array of available tool names
- `mcp_servers`: Array of MCP server status objects

Example:
```json
{"type":"system","subtype":"init","session_id":"01941f2a-3b4c-7d8e-9f0a-1b2c3d4e5f6a","cwd":"/home/user/project","model":"claude-sonnet-4-20250514","permissionMode":"default","apiKeySource":"environment","tools":["Bash","Read","Write","Edit","Glob","Grep","WebFetch","WebSearch","Task","TodoWrite"],"mcp_servers":[]}
```

### `assistant`

Emitted when Claude produces a response. May contain text and/or tool use.

Fields:
- `type`: `"assistant"`
- `session_id`: UUID string
- `message.id`: Message ID (e.g., `"msg_01ABC..."`)
- `message.type`: `"message"`
- `message.role`: `"assistant"`
- `message.model`: Model identifier
- `message.content`: Array of content blocks (see below)
- `message.usage`: Token usage object

Example (text only):
```json
{"type":"assistant","session_id":"01941f2a-3b4c-7d8e-9f0a-1b2c3d4e5f6a","message":{"id":"msg_01ABC123","type":"message","role":"assistant","model":"claude-sonnet-4-20250514","content":[{"type":"text","text":"I'll help you with that task."}],"usage":{"input_tokens":1500,"output_tokens":50,"cache_read_input_tokens":1000}}}
```

Example (with tool use):
```json
{"type":"assistant","session_id":"01941f2a-3b4c-7d8e-9f0a-1b2c3d4e5f6a","message":{"id":"msg_01DEF456","type":"message","role":"assistant","model":"claude-sonnet-4-20250514","content":[{"type":"text","text":"Let me check the files."},{"type":"tool_use","id":"toolu_01XYZ789","name":"Bash","input":{"command":"ls -la","description":"List files in current directory"}}],"usage":{"input_tokens":1500,"output_tokens":100}}}
```

Example (with thinking, if enabled):
```json
{"type":"assistant","session_id":"01941f2a-3b4c-7d8e-9f0a-1b2c3d4e5f6a","message":{"id":"msg_01GHI012","type":"message","role":"assistant","model":"claude-sonnet-4-20250514","content":[{"type":"thinking","thinking":"I need to analyze the codebase structure first..."},{"type":"text","text":"I'll examine the project structure."}],"usage":{"input_tokens":2000,"output_tokens":150}}}
```

### `user`

Emitted after tool execution with results.

Fields:
- `type`: `"user"`
- `session_id`: UUID string
- `message.id`: Message ID
- `message.type`: `"message"`
- `message.role`: `"user"`
- `message.content`: Array of tool_result blocks

Example:
```json
{"type":"user","session_id":"01941f2a-3b4c-7d8e-9f0a-1b2c3d4e5f6a","message":{"id":"msg_02ABC123","type":"message","role":"user","content":[{"type":"tool_result","tool_use_id":"toolu_01XYZ789","content":"total 48\ndrwxr-xr-x  5 user user 4096 Jan  1 12:00 .\ndrwxr-xr-x 10 user user 4096 Jan  1 11:00 ..\n-rw-r--r--  1 user user  512 Jan  1 12:00 README.md\n-rw-r--r--  1 user user 1024 Jan  1 12:00 main.py"}]}}
```

Example (with error):
```json
{"type":"user","session_id":"01941f2a-3b4c-7d8e-9f0a-1b2c3d4e5f6a","message":{"id":"msg_02DEF456","type":"message","role":"user","content":[{"type":"tool_result","tool_use_id":"toolu_01ABC123","content":"Error: Command failed with exit code 1\nfatal: not a git repository","is_error":true}]}}
```

### `result`

Final event. Contains session summary and final answer.

Fields:
- `type`: `"result"`
- `subtype`: `"success"` or `"error"`
- `session_id`: UUID string
- `total_cost_usd`: Total cost in USD (float)
- `is_error`: Boolean indicating error state
- `duration_ms`: Total duration in milliseconds
- `duration_api_ms`: API call duration in milliseconds
- `num_turns`: Number of turns in the session
- `result`: Final text result/answer
- `usage`: Aggregate token usage
- `modelUsage`: Per-model usage breakdown
- `error`: Error message (only if `is_error=true`)
- `permission_denials`: Array of denied tool calls (if any)
- `uuid`: Session UUID (may differ from session_id)

Example (success):
```json
{"type":"result","subtype":"success","session_id":"01941f2a-3b4c-7d8e-9f0a-1b2c3d4e5f6a","total_cost_usd":0.0234,"is_error":false,"duration_ms":15234,"duration_api_ms":12500,"num_turns":3,"result":"I've completed the task. Here's a summary of what was done:\n\n1. Listed the directory contents\n2. Found 3 Python files\n3. Analyzed the main.py file","usage":{"input_tokens":5000,"output_tokens":1200,"cache_read_input_tokens":3000,"cache_creation_input_tokens":500,"server_tool_use":{"web_search_requests":0}},"modelUsage":{"claude-sonnet-4-20250514":{"inputTokens":5000,"outputTokens":1200,"cacheReadInputTokens":3000,"cacheCreationInputTokens":500,"webSearchRequests":0,"costUSD":0.0234}}}
```

Example (error):
```json
{"type":"result","subtype":"error","session_id":"01941f2a-3b4c-7d8e-9f0a-1b2c3d4e5f6a","total_cost_usd":0.005,"is_error":true,"duration_ms":5000,"duration_api_ms":4500,"num_turns":1,"result":"","error":"Rate limit exceeded. Please try again later.","usage":{"input_tokens":1000,"output_tokens":50}}
```

Example (with permission denials):
```json
{"type":"result","subtype":"success","session_id":"01941f2a-3b4c-7d8e-9f0a-1b2c3d4e5f6a","total_cost_usd":0.01,"is_error":false,"duration_ms":8000,"num_turns":2,"result":"Task completed with some operations skipped due to permissions.","permission_denials":[{"tool_name":"Bash","tool_use_id":"toolu_01ABC123","tool_input":{"command":"rm -rf /tmp/test"}}]}
```

## Content block types (in `message.content`)

### Text content

Fields:
- `type`: `"text"`
- `text`: The text content

Example:
```json
{"type":"text","text":"I'll help you analyze this code."}
```

### Thinking content (if extended thinking enabled)

Fields:
- `type`: `"thinking"`
- `thinking`: The thinking/reasoning text

Example:
```json
{"type":"thinking","thinking":"Let me analyze the structure of this codebase. I should start by looking at the main entry point..."}
```

### Tool use content

Fields:
- `type`: `"tool_use"`
- `id`: Tool use ID (e.g., `"toolu_01XYZ..."`)
- `name`: Tool name
- `input`: Tool input parameters (object)

Example (Bash):
```json
{"type":"tool_use","id":"toolu_01XYZ789","name":"Bash","input":{"command":"git status","description":"Check git status"}}
```

Example (Read):
```json
{"type":"tool_use","id":"toolu_01ABC123","name":"Read","input":{"file_path":"/home/user/project/main.py"}}
```

Example (Write):
```json
{"type":"tool_use","id":"toolu_01DEF456","name":"Write","input":{"file_path":"/home/user/project/output.txt","content":"Hello, World!"}}
```

Example (Edit):
```json
{"type":"tool_use","id":"toolu_01GHI789","name":"Edit","input":{"file_path":"/home/user/project/main.py","old_string":"def hello():\n    pass","new_string":"def hello():\n    print('Hello!')"}}
```

Example (Glob):
```json
{"type":"tool_use","id":"toolu_01JKL012","name":"Glob","input":{"pattern":"**/*.py"}}
```

Example (Grep):
```json
{"type":"tool_use","id":"toolu_01MNO345","name":"Grep","input":{"pattern":"def main","path":"/home/user/project"}}
```

Example (WebSearch):
```json
{"type":"tool_use","id":"toolu_01PQR678","name":"WebSearch","input":{"query":"python asyncio tutorial 2024"}}
```

Example (Task - subagent):
```json
{"type":"tool_use","id":"toolu_01STU901","name":"Task","input":{"description":"Explore codebase","prompt":"Find all API endpoints in this project","subagent_type":"Explore"}}
```

Example (TodoWrite):
```json
{"type":"tool_use","id":"toolu_01VWX234","name":"TodoWrite","input":{"todos":[{"content":"Fix bug","status":"in_progress","activeForm":"Fixing bug"},{"content":"Write tests","status":"pending","activeForm":"Writing tests"}]}}
```

### Tool result content (in user messages)

Fields:
- `type`: `"tool_result"`
- `tool_use_id`: Corresponding tool use ID
- `content`: Result content (string or array)
- `is_error`: Boolean (optional, indicates error)

Example (string content):
```json
{"type":"tool_result","tool_use_id":"toolu_01XYZ789","content":"On branch main\nnothing to commit, working tree clean"}
```

Example (array content - Task tool format):
```json
{"type":"tool_result","tool_use_id":"toolu_01STU901","content":[{"type":"text","text":"Found 5 API endpoints in src/api/"}]}
```

Example (error result):
```json
{"type":"tool_result","tool_use_id":"toolu_01ABC123","content":"Error: File not found: /nonexistent/path","is_error":true}
```

## Usage object structure

### Message-level usage

```json
{
  "input_tokens": 1500,
  "output_tokens": 200,
  "cache_read_input_tokens": 1000,
  "cache_creation_input_tokens": 0,
  "service_tier": "standard"
}
```

### Result-level usage (aggregate)

```json
{
  "input_tokens": 5000,
  "output_tokens": 1200,
  "cache_read_input_tokens": 3000,
  "cache_creation_input_tokens": 500,
  "server_tool_use": {
    "web_search_requests": 2
  },
  "cache_creation": {
    "ephemeral_1h_input_tokens": 100,
    "ephemeral_5m_input_tokens": 50
  }
}
```

### Model usage breakdown

```json
{
  "claude-sonnet-4-20250514": {
    "inputTokens": 5000,
    "outputTokens": 1200,
    "cacheReadInputTokens": 3000,
    "cacheCreationInputTokens": 500,
    "webSearchRequests": 2,
    "costUSD": 0.0234,
    "contextWindow": 200000
  }
}
```

## Consumer considerations (rendering + success/failure)

### What to render for users

- **Session start:** Render `system` (init) as "session started" with resume token
- **Tool execution:** Render `tool_use` in assistant messages as action status lines
- **Final answer:** Render `result.result` as the main response
- **Errors:** Render `result.error` and `permission_denials` as user-visible warnings

### Suggested progress rendering

For each assistant message with tool_use:
1. Extract tool name and key input (command, path, query, etc.)
2. Show as "▸ running: `<command>`" or "▸ reading: `<path>`"

For each user message with tool_result:
1. Match to corresponding tool_use by `tool_use_id`
2. Update status to "✓" (success) or "✗" (error based on `is_error` field or content inspection)

### Fields you can safely skip for UX

- `message.usage` on individual messages (use aggregate from result)
- `thinking` content blocks (internal reasoning)
- `cache_*` token counts (telemetry)
- `modelUsage` breakdown (use `total_cost_usd` instead)

### Success and failure signals

- **Session success:** `result.subtype == "success"` and `result.is_error == false`
- **Session failure:** `result.is_error == true` or `result.subtype == "error"`
- **Tool success:** `tool_result` without `is_error: true` and content not starting with "Error:"
- **Tool failure:** `tool_result` with `is_error: true` or content indicating error

### Suggested minimal event handling

```python
def handle_event(event: dict) -> None:
    match event["type"]:
        case "system" if event.get("subtype") == "init":
            # Session started - extract session_id for resume token
            session_id = event["session_id"]

        case "assistant":
            for content in event["message"]["content"]:
                if content["type"] == "tool_use":
                    # Tool execution started
                    tool_id = content["id"]
                    tool_name = content["name"]
                    tool_input = content.get("input", {})

        case "user":
            for content in event["message"]["content"]:
                if content["type"] == "tool_result":
                    # Tool execution completed
                    tool_id = content["tool_use_id"]
                    is_error = content.get("is_error", False)

        case "result":
            # Session completed
            ok = not event.get("is_error", False)
            answer = event.get("result", "")
            cost = event.get("total_cost_usd", 0)
```

## Comparison with Codex exec --json

| Aspect | Codex | Claude Code |
|--------|-------|-------------|
| Session start | `thread.started` | `system` (subtype: init) |
| Session ID field | `thread_id` | `session_id` |
| Action start | `item.started` | `assistant` with `tool_use` |
| Action complete | `item.completed` | `user` with `tool_result` |
| Action ID | `item.id` | `tool_use.id` / `tool_use_id` |
| Final answer | `item.completed` (agent_message) | `result.result` |
| Turn tracking | `turn.started` / `turn.completed` | Implicit (count messages) |
| Usage | `turn.completed.usage` | `result.usage` |
| Cost | Not provided | `result.total_cost_usd` |
