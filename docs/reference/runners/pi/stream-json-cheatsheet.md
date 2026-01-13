# Pi `--mode json` event cheatsheet

`pi --print --mode json` writes **one JSON object per line** (JSONL) with a
required `type` field. These are `AgentSessionEvent` objects from
`@mariozechner/pi-agent-core`.

## Top-level event lines

### `agent_start`

```json
{"type":"agent_start"}
```

### `agent_end`

```json
{"type":"agent_end","messages":[{"role":"assistant","content":[{"type":"text","text":"Done."}],"stopReason":"stop","timestamp":123}]} 
```

### `turn_start` / `turn_end`

```json
{"type":"turn_start"}
```

```json
{"type":"turn_end","message":{...},"toolResults":[...]} 
```

### `message_start` / `message_update` / `message_end`

```json
{"type":"message_start","message":{"role":"assistant","content":[{"type":"text","text":"Working..."}]}}
```

```json
{"type":"message_update","message":{...},"assistantMessageEvent":{"type":"text_delta","delta":"...","contentIndex":0}} 
```

```json
{"type":"message_end","message":{"role":"assistant","content":[{"type":"text","text":"Done."}],"stopReason":"stop"}}
```

### `tool_execution_start`

```json
{"type":"tool_execution_start","toolCallId":"tool_1","toolName":"bash","args":{"command":"ls"}}
```

### `tool_execution_update`

```json
{"type":"tool_execution_update","toolCallId":"tool_1","toolName":"bash","args":{"command":"ls"},"partialResult":{"content":[{"type":"text","text":"..."}]}} 
```

### `tool_execution_end`

```json
{"type":"tool_execution_end","toolCallId":"tool_1","toolName":"bash","result":{"content":[{"type":"text","text":"ok"}],"details":{}},"isError":false}
```

## Notes

* `message_end` with `role = "assistant"` contains the final assistant text.
* `assistantMessageEvent` in `message_update` provides streaming deltas.
* `tool_execution_*` events map cleanly to Takopi `action` events.
