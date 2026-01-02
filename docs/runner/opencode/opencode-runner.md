# OpenCode Runner

This runner integrates with the [OpenCode CLI](https://github.com/sst/opencode).

## Installation

```bash
go install github.com/sst/opencode@latest
```

## Configuration

Add to your `takopi.toml`:

```toml
[opencode]
model = "claude-sonnet"  # optional
```

## Usage

```bash
takopi opencode
```

## Resume Format

Resume line format: `` `opencode --session ses_XXX` ``

The runner recognizes both `--session` and `-s` flags.

## JSON Event Format

OpenCode outputs JSON events with the following types:

| Event Type | Description |
|------------|-------------|
| `step_start` | Beginning of a processing step |
| `tool_use` | Tool invocation with input/output |
| `text` | Text output from the model |
| `step_finish` | End of a step (reason: "stop" or "tool-calls") |
| `error` | Error event |

See [opencode-stream-json-cheatsheet.md](./opencode-stream-json-cheatsheet.md) for detailed event format documentation.
