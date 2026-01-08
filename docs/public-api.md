# Public Plugin API

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
dependencies = ["takopi>=0.11,<0.12"]
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
| `JsonlSubprocessRunner` | Helper for JSONL‑streaming CLIs |
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

### Core types and helpers

| Symbol | Purpose |
|--------|---------|
| `EngineId` | Engine id type alias |
| `ResumeToken` | Resume token (engine + value) |
| `StartedEvent` / `ActionEvent` / `CompletedEvent` | Core event types |
| `Action` | Action metadata for `ActionEvent` |
| `RunContext` | Project/branch context |
| `ConfigError` | Configuration error type |
| `TakopiSettings` | Parsed settings model |
| `ProjectsConfig` / `ProjectConfig` | Normalized projects config |
| `AutoRouter` | Router over runner entries |
| `RunnerEntry` | Router entry (runner + availability) |
| `RunnerUnavailableError` | Router error when a runner is unavailable |

### Bridge helpers (for transport plugins)

| Symbol | Purpose |
|--------|---------|
| `ExecBridgeConfig` | Transport + presenter config |
| `IncomingMessage` | Normalized incoming message |
| `RunningTask` / `RunningTasks` | Per‑message run coordination |
| `handle_message()` | Core message handler used by transports |

---

## Runner contract (engine plugins)

Runners emit events in a strict sequence (see `tests/test_runner_contract.py`):

- Exactly **one** `StartedEvent`
- Exactly **one** `CompletedEvent`
- `CompletedEvent` is **last**
- `CompletedEvent.resume == StartedEvent.resume`

Action events are optional. The minimal valid run is:

```
StartedEvent → CompletedEvent
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
    def lock_token(self, *, settings: TakopiSettings, config_path: Path) -> str | None: ...
    def build_and_run(...) -> None: ...
```

Transport backends are responsible for:

- Validating config and onboarding users (`check_setup`, `interactive_setup`)
- Providing a lock token so Takopi can prevent parallel runs
- Starting the transport loop in `build_and_run`

---

## Bridge usage (transport plugins)

Most transports can delegate message handling to `handle_message()`:

```py
from takopi.api import (
    ExecBridgeConfig,
    IncomingMessage,
    RunningTask,
    RunningTasks,
    handle_message,
)

async def on_message(...):
    incoming = IncomingMessage(
        channel_id=...,
        message_id=...,
        text=...,
        reply_to=...,
    )
    await handle_message(
        exec_cfg,
        runner=entry.runner,
        incoming=incoming,
        resume_token=resume,
        context=context,
        context_line=context_line,
        strip_resume_line=router.is_resume_line,
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
