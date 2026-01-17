# Plugin system

Takopi uses Python entrypoints to extend engines, transports, and commands.

## Why entrypoints

Entrypoints let Takopi discover plugins without hard dependencies on plugin packages.
Installed distributions declare what they provide, and Takopi can list and load them at runtime.

This makes it possible to:

- Add new engines/transports/commands without changing Takopi itself.
- Ship plugins independently.
- Keep the core CLI small.

## Why discovery is lazy

Takopi lists plugin IDs **without importing plugin code**, then imports a plugin only when:

- it is selected by routing (engine/transport), or
- it is invoked as a command, or
- you explicitly request loading via `takopi plugins --load`.

This keeps `takopi --help` fast and prevents a broken third-party plugin from bricking the CLI.

## Entrypoint rules (what Takopi expects)

Takopi uses three entrypoint groups:

```toml
[project.entry-points."takopi.engine_backends"]
myengine = "myengine.backend:BACKEND"

[project.entry-points."takopi.transport_backends"]
mytransport = "mytransport.backend:BACKEND"

[project.entry-points."takopi.command_backends"]
mycommand = "mycommand.backend:BACKEND"
```

Rules:

- The entrypoint **name** is the plugin id.
- The entrypoint value must resolve to a backend object:
  - engine backend: `EngineBackend`
  - transport backend: `TransportBackend`
  - command backend: `CommandBackend`
- The backend object must have `id == entrypoint name`.

## Why there is an enabled list

Plugin visibility can be restricted via:

=== "takopi config"

    ```sh
    takopi config set plugins.enabled '["takopi-engine-acme", "takopi-transport-slack"]'
    ```

=== "toml"

    ```toml
    [plugins]
    enabled = ["takopi-engine-acme", "takopi-transport-slack"]
    ```

When set, Takopi filters by **distribution name** (package metadata), not by entrypoint name.
This lets you:

- ship multiple entrypoints from one distribution, and
- enable/disable whole plugin packages predictably.

## IDs and collisions

Entrypoint names become plugin IDs and appear in user-facing surfaces (CLI subcommands, Telegram commands, `/<engine-id>` directives).
Takopi validates IDs and rejects collisions with reserved names.

Plugin IDs must match:

```
^[a-z0-9_]{1,32}$
```

Reserved IDs include core chat and CLI command names such as `cancel`, `init`, and `plugins`.

## How to debug discovery and loading

```sh
takopi plugins
takopi plugins --load
```

## Related

- [Write a plugin](../how-to/write-a-plugin.md)
- [Plugin API reference](../reference/plugin-api.md)
