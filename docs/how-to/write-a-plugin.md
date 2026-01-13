# Write a plugin

Takopi supports entrypoint-based plugins for engines, transports, and commands.

## Checklist

1. Pick a plugin id (must match `^[a-z0-9_]{1,32}$`).
2. Add a Python entrypoint in your package’s `pyproject.toml`.
3. Implement a backend object (`BACKEND`) with `id == entrypoint name`.
4. Install your package and validate with `takopi plugins --load`.

## Entrypoint groups

Takopi uses three entrypoint groups:

```toml
[project.entry-points."takopi.engine_backends"]
myengine = "myengine.backend:BACKEND"

[project.entry-points."takopi.transport_backends"]
mytransport = "mytransport.backend:BACKEND"

[project.entry-points."takopi.command_backends"]
mycommand = "mycommand.backend:BACKEND"
```

## Engine backend plugin (runner)

Minimal example:

```py
# myengine/backend.py
from __future__ import annotations

from pathlib import Path

from takopi.api import EngineBackend, EngineConfig, Runner


def build_runner(config: EngineConfig, config_path: Path) -> Runner:
    _ = config_path
    return MyEngineRunner(config)


BACKEND = EngineBackend(
    id="myengine",
    build_runner=build_runner,
    cli_cmd="myengine",
    install_cmd="pip install myengine",
)
```

Engine config is a raw table in `takopi.toml`:

```toml
[myengine]
model = "..."
```

## Transport backend plugin

Transport plugins connect Takopi to other messaging systems (Slack, Discord, …).
For most transports, delegate message handling to `handle_message()` from `takopi.api`.

## Command backend plugin

Command plugins add custom `/command` handlers. They only run when the message starts
with `/<id>` and the id does not collide with engine ids, project aliases, or reserved names.

Minimal example:

```py
# mycommand/backend.py
from __future__ import annotations

from takopi.api import CommandContext, CommandResult


class MyCommand:
    id = "hello"
    description = "say hello"

    async def handle(self, ctx: CommandContext) -> CommandResult | None:
        _ = ctx
        return CommandResult(text="hello")


BACKEND = MyCommand()
```

### Command plugin configuration

Configure under `[plugins.<id>]`:

```toml
[plugins.hello]
greeting = "hello"
```

The parsed dict is available as `ctx.plugin_config` in `handle()`.

## Enable/disable installed plugins

```toml
[plugins]
enabled = ["takopi-transport-slack", "takopi-engine-acme"]
```

- `enabled = []` (default) means “load all installed plugins”.
- If non-empty, only distributions with matching names are visible.

## Validate discovery and loading

```sh
takopi plugins
takopi plugins --load
```

## Related

- [Plugin system (design)](../explanation/plugin-system.md)
- [Plugin API reference](../reference/plugin-api.md)
