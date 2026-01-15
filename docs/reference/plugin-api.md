# Plugin API

Takopi’s **public plugin API** is exported from:

```
takopi.api
```

Anything not imported from `takopi.api` should be considered **internal** and
subject to change. The API version is tracked by `TAKOPI_PLUGIN_API_VERSION`.

---

## Versioning

- Current API version: `TAKOPI_PLUGIN_API_VERSION = 1`
- Plugins should pin to a compatible Takopi range, e.g.:

```toml
dependencies = ["takopi>=0.14,<0.15"]
```

---

## Exported symbols

### Engine backends and runners

| Symbol | Purpose |
|--------|---------|
| `EngineBackend` | Declares an engine backend (id + runner builder) |
| `EngineConfig` | Dict-based engine config table |
| `Runner` | Runner protocol |
| `BaseRunner` | Helper base class with resume locking |
| `JsonlSubprocessRunner` | Helper for JSONL-streaming CLIs |
| `EventFactory` | Helper for building takopi events |

### Transport backends

| Symbol | Purpose |
|--------|---------|
| `TransportBackend` | Transport backend protocol |
| `SetupIssue` | Setup issue for onboarding / validation |
| `SetupResult` | Setup issues + config path |
| `Transport` | Transport protocol (send/edit/delete) |
| `Presenter` | Renders progress to `RenderedMessage` |
| `RenderedMessage` | Rendered text + transport metadata |
| `SendOptions` | Reply/notify/replace flags |
| `MessageRef` | Transport-specific message reference |
| `TransportRuntime` | Transport runtime facade (routers/projects hidden) |
| `ResolvedMessage` | Parsed prompt + resume/context resolution |
| `ResolvedRunner` | Runner selection result |

### Command backends

| Symbol | Purpose |
|--------|---------|
| `CommandBackend` | Slash command plugin protocol |
| `CommandContext` | Context passed to a command handler |
| `CommandExecutor` | Helper to send messages or run engines |
| `CommandResult` | Simple response payload for a command |
| `RunRequest` | Engine run request used by commands |
| `RunResult` | Engine run result (captured output) |
| `RunMode` | `"emit"` (send) or `"capture"` (collect) |

### Core types and helpers

| Symbol | Purpose |
|--------|---------|
| `EngineId` | Engine id type alias |
| `ResumeToken` | Resume token (engine + value) |
| `StartedEvent` / `ActionEvent` / `CompletedEvent` | Core event types |
| `Action` | Action metadata for `ActionEvent` |
| `ActionState` / `ProgressState` / `ProgressTracker` | Progress tracking helpers for presenters |
| `RunContext` | Project/branch context |
| `ConfigError` | Configuration error type |
| `DirectiveError` | Error raised when parsing directives |
| `RunnerUnavailableError` | Router error when a runner is unavailable |

### Bridge helpers (for transport plugins)

| Symbol | Purpose |
|--------|---------|
| `ExecBridgeConfig` | Transport + presenter config |
| `IncomingMessage` | Normalized incoming message |
| `RunningTask` / `RunningTasks` | Per-message run coordination |
| `handle_message()` | Core message handler used by transports |

### Plugin utilities

| Symbol | Purpose |
|--------|---------|
| `HOME_CONFIG_PATH` | Canonical config path (`~/.takopi/takopi.toml`) |
| `RESERVED_COMMAND_IDS` | Set of reserved command IDs |
| `read_config` | Read and parse TOML config file |
| `write_config` | Atomically write config to TOML file |
| `get_logger` | Get a structured logger for a module |
| `bind_run_context` | Bind contextual fields to all log entries |
| `clear_context` | Clear bound log context |
| `suppress_logs` | Context manager to suppress info-level logs |
| `set_run_base_dir` | Set working directory context for path relativization |
| `reset_run_base_dir` | Reset working directory context |
| `ThreadJob` | Job dataclass for ThreadScheduler |
| `ThreadScheduler` | Per-thread message serialization |
| `get_command` | Get command backend by ID |
| `list_command_ids` | Get available command plugin IDs |
| `list_backends` | Discover available engine backends |
| `load_settings` | Load full TakopiSettings from config |
| `install_issue` | Create SetupIssue for missing dependency |

---

## Runner contract (engine plugins)

Runners emit events in a strict sequence (see `tests/test_runner_contract.py`):

- Exactly **one** `StartedEvent`
- Exactly **one** `CompletedEvent`
- `CompletedEvent` is **last**
- `CompletedEvent.resume == StartedEvent.resume`

Action events are optional. The minimal valid run is:

```
StartedEvent -> CompletedEvent
```

### Resume tokens

Runners own the resume format:

- `format_resume(token)` returns a command line users can paste
- `extract_resume(text)` parses resume tokens from user text
- `is_resume_line(line)` lets Takopi strip resume lines before running

---

## EngineBackend

```py
EngineBackend(
    id: str,
    build_runner: Callable[[EngineConfig, Path], Runner],
    cli_cmd: str | None = None,
    install_cmd: str | None = None,
)
```

- `id` must match the entrypoint name and the ID regex.
- `build_runner` should raise `ConfigError` for invalid config.
- `cli_cmd` is used to check whether the engine CLI is on `PATH`.
- `install_cmd` is surfaced in onboarding output.

---

## TransportBackend

```py
class TransportBackend(Protocol):
    id: str
    description: str

    def check_setup(...) -> SetupResult: ...
    def interactive_setup(self, *, force: bool) -> bool: ...
    def lock_token(
        self, *, transport_config: dict[str, object], config_path: Path
    ) -> str | None: ...
    def build_and_run(
        self,
        *,
        transport_config: dict[str, object],
        config_path: Path,
        runtime: TransportRuntime,
        final_notify: bool,
        default_engine_override: str | None,
    ) -> None: ...
```

Transport backends are responsible for:

- Validating config and onboarding users (`check_setup`, `interactive_setup`)
- Providing a lock token so Takopi can prevent parallel runs
- Starting the transport loop in `build_and_run`

---

## CommandBackend

```py
class CommandBackend(Protocol):
    id: str
    description: str

    async def handle(self, ctx: CommandContext) -> CommandResult | None: ...
```

Command handlers receive a `CommandContext` with:

- the raw command text and parsed args
- the original message + reply metadata
- `config_path` for the active `takopi.toml` (when known)
- `plugin_config` from `[plugins.<id>]` (dict, defaults to `{}`)
- `runtime` (engine/project resolution)
- `executor` (send messages or run engines)

Use `ctx.executor.run_one(...)` or `ctx.executor.run_many(...)` to reuse Takopi's
engine pipeline. Use `mode="capture"` to collect results and build a custom reply.

`ctx.message` and `ctx.reply_to` are `MessageRef` objects with:

- `channel_id` (`int | str`, chat/channel id)
- `message_id` (`int | str`, message id)
- `thread_id` (`int | str | None`; set when the transport supports threads, like Telegram topics)
- `raw` (transport-specific payload, may be `None`)

Example: key per-thread state by `(ctx.message.channel_id, ctx.message.thread_id)`.

---

## TransportRuntime helpers

`TransportRuntime` keeps transports away from internal router/project types. Key helpers:

- `resolve_message(text, reply_text)` → `ResolvedMessage` (prompt, resume token, context)
- `resolve_engine(engine_override, context)` → `EngineId`
- `resolve_runner(resume_token, engine_override)` → `ResolvedRunner` (runner + availability info)
- `resolve_run_cwd(context)` → `Path | None` (raises `ConfigError` for project/worktree issues)
- `format_context_line(context)` → `str | None`
- `available_engine_ids()` / `missing_engine_ids()` / `engine_ids` / `default_engine`
- `project_aliases()`
- `config_path` (active config path when available)
- `plugin_config(plugin_id)` → `dict` from `[plugins.<id>]`

---

## Bridge usage (transport plugins)

Most transports can delegate message handling to `handle_message()`. Use
`TransportRuntime` to resolve messages and select a runner:

```py
from takopi.api import (
    ExecBridgeConfig,
    IncomingMessage,
    RunningTask,
    RunningTasks,
    TransportRuntime,
    handle_message,
)

async def on_message(...):
    resolved = runtime.resolve_message(text=text, reply_text=reply_text)
    entry = runtime.resolve_runner(
        resume_token=resolved.resume_token,
        engine_override=resolved.engine_override,
    )
    context_line = runtime.format_context_line(resolved.context)
    incoming = IncomingMessage(
        channel_id=...,
        message_id=...,
        text=...,
        reply_to=...,
        thread_id=...,
    )
    await handle_message(
        exec_cfg,
        runner=entry.runner,
        incoming=incoming,
        resume_token=resolved.resume_token,
        context=resolved.context,
        context_line=context_line,
        strip_resume_line=runtime.is_resume_line,
        running_tasks=running_tasks,
        on_thread_known=on_thread_known,
    )
```

`handle_message()` implements:

- Progress updates and throttling
- Resume handling
- Cancellation propagation
- Final rendering

This keeps transport backends thin and consistent with core behavior.
