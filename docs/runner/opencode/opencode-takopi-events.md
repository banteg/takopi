# OpenCode to Takopi Event Mapping

This document describes how OpenCode JSON events are translated to Takopi's normalized event model.

## Event Translation

### StartedEvent

Emitted on the first `step_start` event that contains a `sessionID`.

```
OpenCode: {"type":"step_start","sessionID":"ses_XXX",...}
Takopi:   StartedEvent(engine="opencode", resume=ResumeToken(engine="opencode", value="ses_XXX"))
```

### ActionEvent

Tool usage is translated to action events.

**Started phase** (when tool is pending/running):
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

**Error**:
```
OpenCode: {"type":"error","message":"API rate limit exceeded"}
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
