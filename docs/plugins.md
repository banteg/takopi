# Plugins

Takopi supports **entrypoint-based plugins** for:

- **Engine backends** (new runner implementations)
- **Transport backends** (new chat/command transports)
- **Command backends** (custom `/command` handlers)

Plugins are **discovered lazily**: Takopi lists IDs without importing plugin code,
and loads a plugin only when it is needed (or when you explicitly request it).

This keeps `takopi --help` fast and prevents broken plugins from bricking the CLI.

See `public-api.md` for the stable API surface you should depend on.

---

## Entrypoint groups

Takopi uses two Python entrypoint groups:

```toml
[project.entry-points."takopi.engine_backends"]
myengine = "myengine.backend:BACKEND"

[project.entry-points."takopi.transport_backends"]
mytransport = "mytransport.backend:BACKEND"

[project.entry-points."takopi.command_backends"]
mycommand = "mycommand.backend:BACKEND"
```

**Rules:**

- The entrypoint **name** is the plugin ID.
- The entrypoint value must resolve to a **backend object**:
  - Engine backend -> `EngineBackend`
  - Transport backend -> `TransportBackend`
- The backend object **must** have `id == entrypoint name`.

Takopi validates this at load time and will report errors via `takopi plugins --load`.

---

## ID rules

Plugin IDs are used in the CLI and (for engines/projects) in Telegram commands.
They must match:

```
^[a-z0-9_]{1,32}$
```

If an ID does not match, it is skipped and reported as an error.

**Reserved IDs (engines):**

- `cancel` (core chat command)
- `init`, `plugins` (CLI commands)

Engines using these IDs are skipped and reported as errors.

**Reserved IDs (commands):**

- `cancel`, `init`, `plugins`
- Any engine id or project alias (checked at runtime)

Command backends using reserved IDs are skipped and reported as errors.

---

## Enabling plugins

Takopi supports a simple enabled list to control which plugins are visible.

```toml
[plugins]
enabled = ["takopi-transport-slack", "takopi-engine-acme"]
auto_install = false
```

- `enabled = []` (default) -> load all installed plugins.
- If `enabled` is non-empty, **only distributions with matching names** are visible.
- Distribution names are taken from package metadata (case-insensitive).
- If a plugin has no resolvable distribution name and an enabled list is set, it is hidden.
- `auto_install` is **reserved** and not implemented yet.

This enabled list affects:

- Engine subcommands registered in the CLI
- `takopi plugins` output
- Runtime resolution of engines/transports/commands

---

## Discovering plugins

Use the CLI to inspect plugins:

```sh
takopi plugins
takopi plugins --load
```

Behavior:

- `takopi plugins` lists discovered entrypoints **without loading them**.
- `--load` loads each plugin to validate type and surface import errors.
- Errors are shown at the end, grouped by engine/transport and distribution.
- If `[plugins] enabled` is set, entries are still listed but marked `enabled`/`disabled`.

---

## Engine backend plugins

Engine plugins implement a runner for a new engine CLI and expose
an `EngineBackend` object.

Minimal example:

```py
# myengine/backend.py
from __future__ import annotations

from pathlib import Path

from takopi.api import EngineBackend, EngineConfig, Runner

def build_runner(config: EngineConfig, config_path: Path) -> Runner:
    _ = config_path
    # Parse config if needed; raise ConfigError for invalid config.
    return MyEngineRunner(config)

BACKEND = EngineBackend(
    id="myengine",
    build_runner=build_runner,
    cli_cmd="myengine",
    install_cmd="pip install myengine",
)
```

`EngineConfig` is the raw config table (dict) from `takopi.toml`:

```toml
[myengine]
model = "..."
```

Read it with `settings.engine_config("myengine", config_path=...)` in Takopi,
or just consume the dict directly in your runner builder.

See `public-api.md` for the runner contract and helper classes like
`JsonlSubprocessRunner` and `EventFactory`.

---

## Transport backend plugins

Transport plugins connect Takopi to new messaging systems (Slack, Discord, etc).

You must provide a `TransportBackend` object with:

- `id` and `description`
- `check_setup()` -> returns `SetupResult` (issues + config path)
- `interactive_setup()` -> optional interactive setup flow
- `lock_token()` -> token fingerprinting for config locks
- `build_and_run()` -> build transport and start the main loop

Minimal skeleton:

```py
# mytransport/backend.py
from __future__ import annotations

from pathlib import Path

from takopi.api import (
    EngineBackend,
    SetupResult,
    TransportBackend,
    TransportRuntime,
)

class MyTransportBackend:
    id = "mytransport"
    description = "MyTransport bot"

    def check_setup(
        self, engine_backend: EngineBackend, *, transport_override: str | None = None
    ) -> SetupResult:
        _ = engine_backend, transport_override
        return SetupResult(issues=[], config_path=Path("takopi.toml"))

    def interactive_setup(self, *, force: bool) -> bool:
        _ = force
        return True

    def lock_token(
        self, *, transport_config: dict[str, object], config_path: Path
    ) -> str | None:
        _ = transport_config, config_path
        return None

    def build_and_run(
        self,
        *,
        transport_config: dict[str, object],
        config_path: Path,
        runtime: TransportRuntime,
        final_notify: bool,
        default_engine_override: str | None,
    ) -> None:
        _ = (
            transport_config,
            config_path,
            runtime,
            final_notify,
            default_engine_override,
        )
        raise NotImplementedError

BACKEND = MyTransportBackend()
```

For most transports, you will want to call `handle_message()` from `takopi.api`
inside your message loop. That function implements progress updates, resume handling,
and cancellation semantics.

---

## Command backend plugins

Command plugins add custom `/command` handlers. A command only runs when the
message starts with `/command` and does **not** collide with engine ids,
project aliases, or reserved command names.

Minimal example:

```py
# mycommand/backend.py
from __future__ import annotations

from takopi.api import CommandContext, CommandResult, RunRequest

class MultiCommand:
    id = "multi"
    description = "run the prompt on every engine"

    async def handle(self, ctx: CommandContext) -> CommandResult | None:
        prompt = ctx.args_text.strip()
        if not prompt:
            return CommandResult(text="usage: /multi <prompt>")
        requests = [
            RunRequest(prompt=prompt, engine=engine)
            for engine in ctx.runtime.available_engine_ids()
        ]
        results = await ctx.executor.run_many(
            requests,
            mode="capture",
            parallel=True,
        )
        blocks = []
        for result in results:
            text = result.message.text if result.message else "no output"
            blocks.append(f"## {result.engine}\n{text}")
        return CommandResult(text="\n\n".join(blocks))

BACKEND = MultiCommand()
```

### Command plugin configuration

Configure command plugins under `[plugins.<id>]`:

```toml
[plugins.multi]
engines = ["codex", "claude"]
```

The parsed dict is available as `ctx.plugin_config` inside `handle()`.

---

## Versioning & compatibility

Takopi exposes a **stable plugin API** via `takopi.api`.

- `TAKOPI_PLUGIN_API_VERSION = 1` is the current API version.
- Depend on a compatible Takopi version range, for example:

```toml
dependencies = ["takopi>=0.11,<0.12"]
```

When the plugin API changes, Takopi will bump the API version and document
any compatibility guidance.

---

## Troubleshooting

Common issues:

- **Plugin missing from CLI**: check the enabled list in `[plugins] enabled`.
- **Plugin not listed**: verify entrypoint group and ID regex.
- **Load failures**: run `takopi plugins --load` and inspect errors.
- **ID mismatch**: ensure `BACKEND.id == entrypoint name`.
