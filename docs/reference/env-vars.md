# Environment variables

Takopi supports a small set of environment variables for logging and runtime behavior.

## Logging

| Variable | Description |
|----------|-------------|
| `TAKOPI_LOG_LEVEL` | Minimum log level (default `info`; `--debug` forces `debug`). |
| `TAKOPI_LOG_FORMAT` | `console` (default) or `json`. |
| `TAKOPI_LOG_COLOR` | Force color on/off (`1/true/yes/on` or `0/false/no/off`). |
| `TAKOPI_LOG_FILE` | Append JSON lines to a file. `--debug` defaults this to `debug.log`. |
| `TAKOPI_TRACE_PIPELINE` | Log pipeline events at `info` instead of `debug`. |

## CLI behavior

| Variable | Description |
|----------|-------------|
| `TAKOPI_NO_INTERACTIVE` | Disable interactive prompts (useful for CI / non-TTY). |

## Engine-specific

| Variable | Description |
|----------|-------------|
| `PI_CODING_AGENT_DIR` | Override Pi agent session directory base path. |

