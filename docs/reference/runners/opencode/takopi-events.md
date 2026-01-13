# OpenCode to Takopi Event Mapping

This document describes how OpenCode JSON events are translated to Takopi's normalized event model.

> **Authoritative source:** The schema definitions are in `src/takopi/schemas/opencode.py` and the translation logic is in `src/takopi/runners/opencode.py`. When in doubt, refer to the code.

## Event Translation

### StartedEvent

Emitted on the first `step_start` event that contains a `sessionID`.

```
OpenCode: {"type":"step_start","sessionID":"ses_XXX",...}
Takopi:   StartedEvent(engine="opencode", resume=ResumeToken(engine="opencode", value="ses_XXX"))
```

### ActionEvent

Tool usage is translated to action events. Note: `opencode run --format json` currently only emits `tool_use` events when the tool finishes (`status == "completed"`). Pending/running tool states exist in the schema but are not emitted by the CLI JSON stream.

**Started phase** (when tool is pending/running, if emitted by the JSON stream):
```
OpenCode: {"type":"tool_use","part":{"tool":"bash","state":{"status":"pending",...}}}
Takopi:   ActionEvent(engine="opencode", action=Action(kind="command"), phase="started")
```

**Completed phase** (when tool finishes):
```
OpenCode: {"type":"tool_use","part":{"tool":"bash","state":{"status":"completed","metadata":{"exit":0}}}}
Takopi:   ActionEvent(engine="opencode", action=Action(kind="command"), phase="completed", ok=True)
```

### CompletedEvent

Emitted on `step_finish` with `reason="stop"` or on `error` events.

**Success**:
```
OpenCode: {"type":"step_finish","part":{"reason":"stop","tokens":{...},"cost":0.001}}
Takopi:   CompletedEvent(engine="opencode", ok=True, answer="<accumulated text>", usage={...})
```

If `step_finish` omits `reason`, Takopi treats a clean process exit as successful completion and emits `CompletedEvent(ok=True)` with the accumulated usage.

**Error**:
```
OpenCode: {"type":"error","error":{"name":"APIError","data":{"message":"API rate limit exceeded"}}}
Takopi:   CompletedEvent(engine="opencode", ok=False, error="API rate limit exceeded")
```

## Tool Kind Mapping

| OpenCode Tool | Takopi ActionKind |
|---------------|-------------------|
| `bash`, `shell` | `command` |
| `edit`, `write`, `multiedit` | `file_change` |
| `read` | `tool` |
| `glob` | `tool` |
| `grep` | `tool` |
| `websearch`, `web_search` | `web_search` |
| `webfetch`, `web_fetch` | `web_search` |
| `todowrite`, `todoread` | `note` |
| `task` | `tool` |
| (other) | `tool` |

## Usage Accumulation

Token usage is accumulated across all `step_finish` events and reported in the final `CompletedEvent.usage`:

```json
{
  "total_cost_usd": 0.001,
  "tokens": {
    "input": 22443,
    "output": 118,
    "reasoning": 0,
    "cache_read": 21415,
    "cache_write": 0
  }
}
```
