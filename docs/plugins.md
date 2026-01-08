# Plugins

Takopi supports **entrypoint-based plugins** for:

- **Engine backends** (new runner implementations)
- **Transport backends** (new chat/command transports)

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

---

## Allowlisting plugins

Takopi supports a simple allowlist to control which plugins are visible.

```toml
[plugins]
enabled = ["takopi-transport-slack", "takopi-engine-acme"]
auto_install = false
```

- `enabled = []` (default) -> load all installed plugins.
- If `enabled` is non-empty, **only distributions with matching names** are visible.
- Distribution names are taken from package metadata (case-insensitive).
- If a plugin has no resolvable distribution name and an allowlist is set, it is hidden.
- `auto_install` is **reserved** and not implemented yet.

This allowlist affects:

- Engine subcommands registered in the CLI
- `takopi plugins` output
- Runtime resolution of engines/transports

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
    AutoRouter,
    EngineBackend,
    ProjectsConfig,
    SetupResult,
    TakopiSettings,
    TransportBackend,
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

    def lock_token(self, *, settings: TakopiSettings, config_path: Path) -> str | None:
        _ = settings, config_path
        return None

    def build_and_run(
        self,
        *,
        settings: TakopiSettings,
        config_path: Path,
        router: AutoRouter,
        projects: ProjectsConfig,
        final_notify: bool,
        default_engine_override: str | None,
    ) -> None:
        _ = settings, config_path, router, projects, final_notify, default_engine_override
        raise NotImplementedError

BACKEND = MyTransportBackend()
```

For most transports, you will want to call `handle_message()` from `takopi.api`
inside your message loop. That function implements progress updates, resume handling,
and cancellation semantics.

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

- **Plugin missing from CLI**: check the allowlist in `[plugins] enabled`.
- **Plugin not listed**: verify entrypoint group and ID regex.
- **Load failures**: run `takopi plugins --load` and inspect errors.
- **ID mismatch**: ensure `BACKEND.id == entrypoint name`.
