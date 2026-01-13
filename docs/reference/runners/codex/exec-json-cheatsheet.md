# Codex `exec --json` event cheatsheet

`codex exec --json` writes **one JSON object per line** (JSONL) to stdout. Each
line is a top-level **thread event** with a `type` field.

Below: **required + commonly emitted fields** for every line type plus a
**full-line example** for each shape that can be emitted. Fields noted as
optional may be omitted (or `null`) depending on Codex version and lifecycle.
Unknown fields may appear; ignore what you don't use.

## Top-level event lines (non-item)

### `thread.started`

Fields:
- `type`
- `thread_id`

Example:
```json
{"type":"thread.started","thread_id":"0199a213-81c0-7800-8aa1-bbab2a035a53"}
```

### `turn.started`

Fields:
- `type`

Example:
```json
{"type":"turn.started"}
```

### `turn.completed`

Fields:
- `type`
- `usage.input_tokens`
- `usage.cached_input_tokens`
- `usage.output_tokens`

Example:
```json
{"type":"turn.completed","usage":{"input_tokens":24763,"cached_input_tokens":24448,"output_tokens":122}}
```

### `turn.failed`

Fields:
- `type`
- `error.message`

Example:
```json
{"type":"turn.failed","error":{"message":"model response stream ended unexpectedly"}}
```

### `error`

Fields:
- `type`
- `message`

Example:
```json
{"type":"error","message":"stream error: broken pipe"}
```

Note: Codex may emit transient reconnect notices as `type="error"` with messages
like `"Reconnecting... 1/5"` while it retries a dropped stream. Treat those as
non-fatal progress updates (the turn continues).

## Item event lines (`item.*`)

Every item line includes:
- `type` (`item.started`, `item.updated`, or `item.completed`)
- `item.id`
- `item.type`
- fields for the specific `item.type` below

`item.id` is stable for the item; updates/completion reuse the same id.

### `agent_message` (only `item.completed`)

Fields:
- `item.text`

Example:
```json
{"type":"item.completed","item":{"id":"item_3","type":"agent_message","text":"Done. I updated the docs and added examples."}}
```

### `reasoning` (only `item.completed`, if enabled)

Fields:
- `item.text`

Example:
```json
{"type":"item.completed","item":{"id":"item_0","type":"reasoning","text":"**Scanning docs for exec JSON schema**"}}
```

### `command_execution` (`item.started` and `item.completed`)

Fields:
- `item.command`
- `item.aggregated_output`
- `item.exit_code` (null or omitted until completion)
- `item.status` (`in_progress`, `completed`, `failed`)

Example (started):
```json
{"type":"item.started","item":{"id":"item_1","type":"command_execution","command":"bash -lc ls","aggregated_output":"","exit_code":null,"status":"in_progress"}}
```

Example (completed, success):
```json
{"type":"item.completed","item":{"id":"item_1","type":"command_execution","command":"bash -lc ls","aggregated_output":"docs\nsrc\n","exit_code":0,"status":"completed"}}
```

Example (completed, failure):
```json
{"type":"item.completed","item":{"id":"item_2","type":"command_execution","command":"bash -lc false","aggregated_output":"","exit_code":1,"status":"failed"}}
```

Note: `aggregated_output` is truncated to **64 KiB**; truncated output ends with
`\n...(truncated)`.

### `file_change` (only `item.completed`)

Fields:
- `item.changes[].path`
- `item.changes[].kind` (`add`, `delete`, `update`)
- `item.status` (`completed`, `failed`)

Example:
```json
{"type":"item.completed","item":{"id":"item_4","type":"file_change","changes":[{"path":"docs/exec-json-cheatsheet.md","kind":"add"},{"path":"docs/exec.md","kind":"update"}],"status":"completed"}}
```

### `mcp_tool_call` (`item.started` and `item.completed`)

Fields:
- `item.server`
- `item.tool`
- `item.arguments` (JSON value; defaults to `null` if absent)
- `item.result` (object or `null`; may be omitted)
- `item.result.content` (array of MCP content blocks)
- `item.result.structured_content` (JSON value or `null`)
- `item.error` (object or `null`; may be omitted)
- `item.error.message` (if `error` is present)
- `item.status` (`in_progress`, `completed`, `failed`)

Example (started):
```json
{"type":"item.started","item":{"id":"item_5","type":"mcp_tool_call","server":"docs","tool":"search","arguments":{"q":"exec --json"},"result":null,"error":null,"status":"in_progress"}}
```

Example (completed, success):
```json
{"type":"item.completed","item":{"id":"item_5","type":"mcp_tool_call","server":"docs","tool":"search","arguments":{"q":"exec --json"},"result":{"content":[{"type":"text","text":"Found 3 matches.","annotations":{"audience":["assistant"],"lastModified":"2025-01-01T00:00:00Z","priority":0.5}}],"structured_content":{"matches":3}},"error":null,"status":"completed"}}
```

Example (completed, failure):
```json
{"type":"item.completed","item":{"id":"item_6","type":"mcp_tool_call","server":"docs","tool":"search","arguments":{"q":"exec --json"},"result":null,"error":{"message":"tool timeout"},"status":"failed"}}
```

### `web_search` (only `item.completed`)

Fields:
- `item.query`

Example:
```json
{"type":"item.completed","item":{"id":"item_7","type":"web_search","query":"codex exec --json schema"}}
```

### `todo_list` (`item.started`, `item.updated`, and `item.completed`)

Fields:
- `item.items[].text`
- `item.items[].completed`

Example (started):
```json
{"type":"item.started","item":{"id":"item_8","type":"todo_list","items":[{"text":"Scan docs","completed":false},{"text":"Write cheatsheet","completed":false}]}}
```

Example (updated):
```json
{"type":"item.updated","item":{"id":"item_8","type":"todo_list","items":[{"text":"Scan docs","completed":true},{"text":"Write cheatsheet","completed":false}]}}
```

Example (completed):
```json
{"type":"item.completed","item":{"id":"item_8","type":"todo_list","items":[{"text":"Scan docs","completed":true},{"text":"Write cheatsheet","completed":true}]}}
```

### `error` (non-fatal warning as an item; only `item.completed`)

Fields:
- `item.message`

Example:
```json
{"type":"item.completed","item":{"id":"item_9","type":"error","message":"command output truncated"}}
```

## MCP content block shapes (`mcp_tool_call.result.content`)

`result.content` is an array of **content blocks**. Each block is one of the
types below; all optional fields may appear depending on the server.

### Text content

Fields:
- `type`
- `text`
- `annotations.audience` (optional)
- `annotations.lastModified` (optional)
- `annotations.priority` (optional)

Example block:
```json
{"type":"text","text":"Hello","annotations":{"audience":["assistant"],"lastModified":"2025-01-01T00:00:00Z","priority":0.5}}
```

### Image content

Fields:
- `type`
- `data` (base64)
- `mimeType`
- `annotations.*` (same as above, optional)

Example block:
```json
{"type":"image","data":"<base64>","mimeType":"image/png","annotations":{"audience":["assistant"]}}
```

### Audio content

Fields:
- `type`
- `data` (base64)
- `mimeType`
- `annotations.*` (optional)

Example block:
```json
{"type":"audio","data":"<base64>","mimeType":"audio/wav","annotations":{"audience":["assistant"]}}
```

### Resource link

Fields:
- `type`
- `name`
- `uri`
- `description` (optional)
- `mimeType` (optional)
- `size` (optional)
- `title` (optional)
- `annotations.*` (optional)

Example block:
```json
{"type":"resource_link","name":"docs/exec.md","uri":"file:///repo/docs/exec.md","description":"Exec docs","mimeType":"text/markdown","size":1234,"title":"exec.md","annotations":{"audience":["assistant"]}}
```

### Embedded resource

Fields:
- `type`
- `resource` (either text or blob contents)
- `annotations.*` (optional)

Example block (embedded text):
```json
{"type":"resource","resource":{"uri":"file:///repo/README.md","text":"Hello","mimeType":"text/markdown"},"annotations":{"audience":["assistant"]}}
```

Example block (embedded blob):
```json
{"type":"resource","resource":{"uri":"file:///repo/image.png","blob":"<base64>","mimeType":"image/png"},"annotations":{"audience":["assistant"]}}
```

## Consumer considerations (rendering + success/failure)

Use this section to decide what to surface to end users vs. what to treat as
machine-only metadata.

### What to render for users

- **Final answer:** render `item.completed` where `item.type = "agent_message"` as
  the main response.
- **Progress updates (optional):**
  - `item.completed` with `item.type = "reasoning"` can be shown as brief
    activity breadcrumbs (only if you want to expose reasoning summaries).
  - `item.started` / `item.completed` with `item.type = "command_execution"` can
    be shown as “running command …” status lines without printing full output.
  - `item.completed` with `item.type = "file_change"` can be rendered as a list
    of changed paths and kinds (add/update/delete).
  - `item.*` with `item.type = "todo_list"` can be shown as a progress checklist.
- **Errors:** render `type = "error"` and `item.type = "error"` as user-visible
  warnings or failures.

### Fields you can safely skip for UX

- `command_execution.aggregated_output` is often noisy; many consumers omit or
  truncate it, and rely on `command_execution.status` + `exit_code` instead.
- `mcp_tool_call.result.content` can be large and tool-specific; consider showing
  only high-level status unless you know the tool’s schema.
- `usage` fields (`turn.completed.usage.*`) are typically telemetry-only.

### Success and failure signals

- **Turn success:** `type = "turn.completed"` indicates overall success.
- **Turn failure:** `type = "turn.failed"` with `error.message` indicates failure.
- **Item success/failure:** use `item.status` on the item payload:
  - `command_execution.status`: `completed` = success, `failed` = failure.
  - `file_change.status`: `completed` = patch applied, `failed` = patch failed.
  - `mcp_tool_call.status`: `completed` = tool succeeded, `failed` = tool failed.
- **Fatal stream errors:** `type = "error"` means the JSONL stream itself hit an
  unrecoverable error (except transient `"Reconnecting... X/Y"` notices, which
  are non-fatal).

### Suggested minimal rendering

If you want a compact UI, the following is usually enough:
- Thread/turn lifecycle: `thread.started`, `turn.started`, `turn.completed` or
  `turn.failed`
- Final answer: `item.completed` with `item.type = "agent_message"`
- Optional progress: `item.started` / `item.completed` for `command_execution`
  and `file_change`

### Optional/conditional emission notes

- `turn.failed` only appears on failure; otherwise `turn.completed` is emitted.
- `reasoning` items only appear when reasoning summaries are enabled.
- `todo_list` items only appear when the plan tool is active; they are the
  primary source of `item.updated`.
- `file_change` and `web_search` items are emitted only as `item.completed`
  in the current `codex exec --json` stream.
