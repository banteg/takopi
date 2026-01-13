# OpenCode `run --format json` Event Cheatsheet

`opencode run --format json` writes one JSON object per line (JSONL) to stdout.
Each line has a `type` field indicating the event type.

## Event Types

### `step_start`

Marks the beginning of a processing step.

Fields:
- `type`: `"step_start"`
- `timestamp`: Unix timestamp in milliseconds
- `sessionID`: Session identifier (format: `ses_XXX`)
- `part.id`: Part identifier
- `part.sessionID`: Session ID (duplicated)
- `part.messageID`: Message ID
- `part.type`: `"step-start"`
- `part.snapshot`: Git snapshot hash

Example:
```json
{"type":"step_start","timestamp":1767036059338,"sessionID":"ses_494719016ffe85dkDMj0FPRbHK","part":{"id":"prt_b6b8e7ec7001qAZUB7eTENxPpI","sessionID":"ses_494719016ffe85dkDMj0FPRbHK","messageID":"msg_b6b8e702b0012XuEC4bGe0XhKa","type":"step-start","snapshot":"71db24a798b347669c0ebadb2dfad238f991753d"}}
```

### `tool_use`

Tool invocation event. Emitted when a tool finishes (`status == "completed"`).

Fields:
- `type`: `"tool_use"`
- `timestamp`: Unix timestamp in milliseconds
- `sessionID`: Session identifier
- `part.id`: Part identifier
- `part.callID`: Unique call ID for this tool invocation
- `part.tool`: Tool name (e.g., "bash", "read", "write", "grep")
- `part.state.status`: `"completed"` (the CLI JSON output does not emit pending/running tool states)
- `part.state.input`: Tool input parameters
- `part.state.output`: Tool output (when completed)
- `part.state.title`: Human-readable description
- `part.state.metadata`: Additional metadata (exit codes, etc.)
- `part.state.time.start`: Start timestamp
- `part.state.time.end`: End timestamp

Example:
```json
{"type":"tool_use","timestamp":1767036061199,"sessionID":"ses_494719016ffe85dkDMj0FPRbHK","part":{"id":"prt_b6b8e85bb001CzBoN2dDlEZJnP","sessionID":"ses_494719016ffe85dkDMj0FPRbHK","messageID":"msg_b6b8e702b0012XuEC4bGe0XhKa","type":"tool","callID":"r9bQWsNLvOrJGIOz","tool":"bash","state":{"status":"completed","input":{"command":"echo hello","description":"Print hello to stdout"},"output":"hello\n","title":"Print hello to stdout","metadata":{"output":"hello\n","exit":0,"description":"Print hello to stdout"},"time":{"start":1767036061123,"end":1767036061173}}}}
```

### `text`

Text output from the model.

Fields:
- `type`: `"text"`
- `timestamp`: Unix timestamp in milliseconds
- `sessionID`: Session identifier
- `part.id`: Part identifier
- `part.type`: `"text"`
- `part.text`: The actual text content
- `part.time.start`: Start timestamp
- `part.time.end`: End timestamp

Example:
```json
{"type":"text","timestamp":1767036064268,"sessionID":"ses_494719016ffe85dkDMj0FPRbHK","part":{"id":"prt_b6b8e8ff2002mxSx9LtvAlf8Ng","sessionID":"ses_494719016ffe85dkDMj0FPRbHK","messageID":"msg_b6b8e8627001yM4qKJCXdC7W1L","type":"text","text":"```\nhello\n```","time":{"start":1767036064265,"end":1767036064265}}}
```

### `step_finish`

Marks the end of a processing step.

Fields:
- `type`: `"step_finish"`
- `timestamp`: Unix timestamp in milliseconds
- `sessionID`: Session identifier
- `part.id`: Part identifier
- `part.type`: `"step-finish"`
- `part.reason`: Optional. `"stop"` (final) or `"tool-calls"` (continuing) when present.
- `part.snapshot`: Git snapshot hash
- `part.cost`: Cost in USD
- `part.tokens.input`: Input token count
- `part.tokens.output`: Output token count
- `part.tokens.reasoning`: Reasoning token count
- `part.tokens.cache.read`: Cache read tokens
- `part.tokens.cache.write`: Cache write tokens

Example (final step):
```json
{"type":"step_finish","timestamp":1767036064273,"sessionID":"ses_494719016ffe85dkDMj0FPRbHK","part":{"id":"prt_b6b8e9209001ojZ4ECN1geZISm","sessionID":"ses_494719016ffe85dkDMj0FPRbHK","messageID":"msg_b6b8e8627001yM4qKJCXdC7W1L","type":"step-finish","reason":"stop","snapshot":"09dd05d11a4ac013136c1df10932efc0ad9116e8","cost":0.001,"tokens":{"input":671,"output":8,"reasoning":0,"cache":{"read":21415,"write":0}}}}
```

Example (tool-calls step):
```json
{"type":"step_finish","timestamp":1767036061205,"sessionID":"ses_494719016ffe85dkDMj0FPRbHK","part":{"id":"prt_b6b8e85fb001L4I3WHMqH6EQNI","sessionID":"ses_494719016ffe85dkDMj0FPRbHK","messageID":"msg_b6b8e702b0012XuEC4bGe0XhKa","type":"step-finish","reason":"tool-calls","snapshot":"ee3406d50c7d9048674bbb1a3e325d82513b74ed","cost":0,"tokens":{"input":21772,"output":110,"reasoning":0,"cache":{"read":0,"write":0}}}}
```

### `error`

Session error event.

Fields:
- `type`: `"error"`
- `timestamp`: Unix timestamp in milliseconds
- `sessionID`: Session identifier
- `error.name`: Error type
- `error.data.message`: Human-readable error (when available)

Example:
```json
{"type":"error","timestamp":1767036065000,"sessionID":"ses_494719016ffe85dkDMj0FPRbHK","error":{"name":"APIError","data":{"message":"Rate limit exceeded","statusCode":429,"isRetryable":true}}}
```

## Mapping to Takopi Events

| OpenCode Event | Takopi Event | Condition |
|----------------|--------------|-----------|
| `step_start` | `StartedEvent` | First occurrence |
| `tool_use` | `ActionEvent(phase="completed")` | `status == "completed"` |
| `text` | (accumulate text) | - |
| `step_finish` | `CompletedEvent` | `reason == "stop"` |
| `step_finish` | (ignored) | `reason == "tool-calls"` |
| `error` | `CompletedEvent(ok=False)` | - |

If `step_finish` omits `reason`, Takopi treats a clean process exit as successful completion and emits `CompletedEvent(ok=True)` with accumulated usage.

## Session ID Format

OpenCode uses session IDs in the format: `ses_XXXXXXXXXXXXXXXXXXXX`

Example: `ses_494719016ffe85dkDMj0FPRbHK`

## Tool Types

Common tool names in OpenCode:
- `bash`: Shell command execution
- `read`: Read file contents
- `write`: Write file contents
- `edit`: Edit file contents
- `glob`: File pattern matching
- `grep`: Content search
- `webfetch`: Fetch web content
- `websearch`: Web search
- `task`: Spawn sub-agent tasks
