# Claude `stream-json` event cheatsheet

`claude -p --output-format stream-json --verbose` writes **one JSON object per line**
(JSONL) with a required `type` field. (`--output-format` only works with `-p`.)

This cheatsheet is derived from `humanlayer/claudecode-go/types.go` and
`client_test.go`.

## Top-level event lines

### `system` (init)

Fields:
- `type`: `"system"`
- `subtype`: `"init"`
- `session_id`
- `tools`: array of tool names
- `mcp_servers`: array of `{name, status}`
- `cwd`, `model`, `permissionMode`, `apiKeySource` (optional)

Example:
```json
{"type":"system","subtype":"init","session_id":"session_01","cwd":"/repo","model":"sonnet","permissionMode":"auto","apiKeySource":"env","tools":["Bash","Read","Write","WebSearch"],"mcp_servers":[{"name":"approvals","status":"connected"}]}
```

### `assistant` / `user`

Fields:
- `type`: `"assistant"` or `"user"`
- `session_id`
- `message` (see below)

Example (assistant text):
```json
{"type":"assistant","session_id":"session_01","message":{"id":"msg_1","type":"message","role":"assistant","content":[{"type":"text","text":"Planning next steps."}],"usage":{"input_tokens":120,"output_tokens":45}}}
```

Example (assistant tool use):
```json
{"type":"assistant","session_id":"session_01","message":{"id":"msg_2","type":"message","role":"assistant","content":[{"type":"tool_use","id":"toolu_1","name":"Bash","input":{"command":"ls -la"}}]}}
```

Example (user tool result, string content):
```json
{"type":"user","session_id":"session_01","message":{"id":"msg_3","type":"message","role":"user","content":[{"type":"tool_result","tool_use_id":"toolu_1","content":"total 2\nREADME.md\nsrc\n"}]}}
```

Example (user tool result, array content):
```json
{"type":"user","session_id":"session_01","message":{"id":"msg_4","type":"message","role":"user","content":[{"type":"tool_result","tool_use_id":"toolu_2","content":[{"type":"text","text":"Task completed"}]}]}}
```

Optional parent field (for nested tool usage):
```json
{"type":"assistant","parent_tool_use_id":"toolu_parent","session_id":"session_01", ...}
```

### `result`

Fields (success path):
- `type`: `"result"`
- `subtype`: `"success"` (or `"completion"`)
- `session_id`
- `total_cost_usd`, `is_error`, `duration_ms`, `duration_api_ms`, `num_turns`
- `result`: final answer string
- `usage`: usage object
- `modelUsage`: optional per-model usage

Example (success):
```json
{"type":"result","subtype":"success","session_id":"session_01","total_cost_usd":0.0123,"is_error":false,"duration_ms":12345,"duration_api_ms":12000,"num_turns":2,"result":"Done.","usage":{"input_tokens":150,"output_tokens":70,"service_tier":"standard","server_tool_use":{"web_search_requests":0}}}
```

Example (error + permission denials):
```json
{"type":"result","subtype":"error","session_id":"session_02","total_cost_usd":0.001,"is_error":true,"duration_ms":2000,"duration_api_ms":1800,"num_turns":1,"result":"","error":"Permission denied","permission_denials":[{"tool_name":"Bash","tool_use_id":"toolu_9","tool_input":{"command":"git fetch origin main"}}]}
```

## Message object (`message` field)

Fields:
- `id`, `type`, `role`
- `model` (optional)
- `content`: array of content blocks
- `usage` (assistant messages)

## Content block shapes (in `message.content[]`)

### Text
```json
{"type":"text","text":"Hello"}
```

### Tool use
```json
{"type":"tool_use","id":"toolu_1","name":"Bash","input":{"command":"ls -la"}}
```

### Tool result
String content:
```json
{"type":"tool_result","tool_use_id":"toolu_1","content":"ok"}
```

Array content (Task tool format):
```json
{"type":"tool_result","tool_use_id":"toolu_2","content":[{"type":"text","text":"Task done"}]}
```
