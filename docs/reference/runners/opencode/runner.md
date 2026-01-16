# OpenCode Runner

This runner integrates with the [OpenCode CLI](https://github.com/sst/opencode).
Shipped in Takopi v0.5.0.

## Installation

```bash
npm i -g opencode-ai@latest
```

## Configuration

Add to your `takopi.toml`:

=== "takopi config"

    ```sh
    takopi config set opencode.model "claude-sonnet"
    ```

=== "toml"

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

The runner recognizes both `--session` and `-s` flags (with or without `run`).

Note: The resume line is meant to reopen the interactive TUI session. `opencode run` is headless and requires a message or command, so it is not the canonical resume command.

## JSON Event Format

OpenCode outputs JSON events with the following types:

| Event Type | Description |
|------------|-------------|
| `step_start` | Beginning of a processing step |
| `tool_use` | Tool invocation with input/output |
| `text` | Text output from the model |
| `step_finish` | End of a step (reason: "stop" or "tool-calls" when present) |
| `error` | Error event |

See [stream-json-cheatsheet.md](./stream-json-cheatsheet.md) for detailed event format documentation.
