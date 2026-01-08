## Review: what’s solid, and what will bite you when you add plugins

### What’s already in a good place

* **Clear boundaries**: `runner_bridge.py` + `transport.py` + `presenter.py` is a strong split. A new transport can reuse `handle_message()` and the event model without touching runner internals.
* **Runner contract is minimal and practical**: `Runner` + `EventFactory` + `JsonlSubprocessRunner` is exactly the sort of surface area third‑party runners can implement/reuse.
* **Engine config shape is future-proof**: `TakopiSettings` uses `extra="allow"`, and `settings.engine_config(engine_id)` returns a raw dict. That’s ideal for plugin runners (they can parse their own `[engine]` table without core changes).
* **Project alias + default engine resolution is clean**: `to_projects_config()` validates collisions and normalizes engine ids.

### Issues to address before/while adding entrypoint plugins

#### 1) Discovery + CLI registration happens at import time (this becomes painful with plugins)

Right now `cli.py` does:

```py
register_engine_commands()
```

at import-time, which calls `list_backends()`, which imports all runner modules (`takopi.runners.*`). With entrypoint plugins, that means:

* `takopi --help` (or even importing `takopi.cli`) could import **every installed plugin**.
* a broken plugin import can break the entire CLI, even when the plugin isn’t used.
* you can’t easily gate plugin loading based on config, because import-time happens before you load settings in a controlled way.

This is the single biggest integration friction point.

**Recommendation**: move to *lazy / two-phase discovery*:

* Phase A: list IDs (for CLI subcommands and help) **without importing plugin modules**.
* Phase B: load a specific backend only when you actually need to build the router / run.

Entry points are perfect for this because you can list `ep.name` without `ep.load()`.

#### 2) Engine discovery is “scan a package and import modules”

`engines.py` uses `pkgutil.iter_modules()` + `importlib.import_module()` to find `BACKEND`.
That’s fine for built-ins, but plugins need a different path anyway. If you keep both, you’ll have two mechanisms with different properties.

**Recommendation**: switch engines to entrypoints for *everything* (core + plugins) so discovery is unified and can be lazy.

#### 3) Transport registry is global and “register builtins once”

`transports.py` hardcodes telegram as a builtin.
That won’t scale if you want 3rd party transports, and it pushes you toward “import this module to register things”.

**Recommendation**: same as engines: use entrypoints for transports too.

#### 4) Telegram “slash commands” are conflated with “directives”

Your `/codex` and `/alias` behavior is implemented as *directive parsing in text* (`_parse_directives`) plus Telegram command menu generation (`_build_bot_commands`).

That’s fine for core, but if you want extensible commands:

* You’ll need a **registry** for “recognized directives”
* You’ll need a way to generate Telegram’s bot command menu from that registry
* You’ll need to keep the parsing deterministic and bounded (no “every plugin gets to run arbitrary parsing logic on every message” without controls)

#### 5) There isn’t a declared “public API boundary”

Right now, plugin authors would inevitably import random internal modules (e.g. `takopi.telegram.*`, `runner_bridge`, etc.) because they’re convenient. That will freeze your internals unintentionally.

**Recommendation**: introduce an explicit “plugin/public API module” and treat everything else as internal.

#### 6) Minor cleanup / correctness nits (worth fixing as you touch these areas)

* `telegram/bridge.py` has `_strip_engine_command()` defined but unused. Either remove it or fold it into the directive parsing refactor.
* `engines.get_engine_config()` duplicates `TakopiSettings.engine_config()`. Decide on one canonical mechanism (I’d keep the settings method).
* IDs: Telegram bot commands only accept `^[a-z0-9_]{1,32}$`. Engine ids/transports ids that contain `-` or uppercase won’t show up in the bot menu. If you embrace plugins, you should **validate IDs** on registration and document the constraints.

---

## Concrete plan for next version: entrypoints-based plugins

I’d structure this as “Plugin API v1” with a small, stable surface: **engine backends** and **transport backends** first. Then add “commands/directives” as a second wave.

### Goal

* Allow `pip install takopi-runner-foo` to add an engine `foo`
* Allow `pip install takopi-transport-bar` to add a transport `bar`
* Keep `takopi --help` reliable and fast even when plugins are installed
* Keep plugin breakage isolated (a broken plugin shouldn’t brick the core)

### Step 0: Decide and document stable IDs + naming rules

Define constraints for:

* Engine ID
* Transport ID
* Slash command name (if/when added)

Recommended:

* `^[a-z0-9_]{1,32}$` (match Telegram command constraints)
* reserve: `cancel`, and reserve all core engine ids + built-in transport ids

Enforce this at registration time with a clear error message.

### Step 1: Introduce entrypoint groups (core + third party)

Add to `pyproject.toml`:

```toml
[project.entry-points."takopi.engine_backends"]
codex = "takopi.runners.codex:BACKEND"
claude = "takopi.runners.claude:BACKEND"
opencode = "takopi.runners.opencode:BACKEND"
pi = "takopi.runners.pi:BACKEND"
mock = "takopi.runners.mock:BACKEND"

[project.entry-points."takopi.transport_backends"]
telegram = "takopi.telegram.backend:telegram_backend"
```

Then a third party package can do:

```toml
[project.entry-points."takopi.engine_backends"]
aider = "takopi_aider.backend:BACKEND"
```

### Step 2: Implement a plugin loader with lazy loading + error isolation

Add `takopi/plugins.py` (or `takopi/entrypoints.py`) that centralizes metadata discovery.

Key design points:

* **List IDs without importing**: use `importlib.metadata.entry_points(group=...)` and read `ep.name`.
* **Load on demand**: call `ep.load()` only when you need the backend.
* **Cache loaded objects**.
* **Capture and surface errors**: keep a “load errors” list so `takopi plugins` can show what failed.

Suggested API shape:

```py
# takopi/plugins.py
from __future__ import annotations
from dataclasses import dataclass
from importlib.metadata import entry_points, EntryPoint
from typing import Any, Callable

@dataclass(frozen=True, slots=True)
class PluginLoadError:
    group: str
    name: str
    value: str
    error: str

# groups
ENGINE_GROUP = "takopi.engine_backends"
TRANSPORT_GROUP = "takopi.transport_backends"

def iter_entrypoints(group: str) -> list[EntryPoint]: ...
def list_ids(group: str) -> list[str]: ...
def load_entrypoint(group: str, name: str) -> Any: ...
def get_load_errors() -> tuple[PluginLoadError, ...]: ...
```

### Step 3: Refactor `engines.py` to use entrypoints and become lazy

Replace `_discover_backends()` with entrypoint-backed registry.

New behavior:

* `list_backend_ids()` returns IDs from entrypoint names (no imports)
* `get_backend(id)` loads the entrypoint object (imports only that backend module), validates type, caches it
* `list_backends()` loads all backends (imports everything) — used only when you truly need the objects (router build, onboarding table, etc.)

This is a big improvement over “import all runners at CLI import time”.

### Step 4: Refactor `transports.py` similarly

Same pattern:

* `list_transports()` returns IDs from entrypoint names
* `get_transport()` loads the backend object by entrypoint name, caches it
* No implicit “register builtins once” global registry needed.

### Step 5: Fix CLI import-time side effects

Change CLI so it doesn’t import/instantiate backends at module import time.

Two approaches (pick one):

**Option A (minimal change)**: keep dynamic per-engine commands, but register them from IDs only

* In `cli.py`, replace `for backend in list_backends():` with `for engine_id in list_backend_ids():`.
* This avoids importing all backends at import time.
* When a user runs `takopi <engine>`, `_run_auto_router()` later calls `get_backend(engine_id)` and loads only that one.

**Option B (cleaner click/typer architecture)**: build the Typer app in `main()`

* `def create_app(): ...` registers commands after `setup_logging()` if you want.
* Slightly more refactor but gives you more control and makes plugin-error reporting nicer.

Given your codebase, Option A is probably the best “next version” move.

### Step 6: Add a `takopi plugins` (or `takopi doctor`) command

This is hugely useful once plugins exist.

Show:

* discovered engine ids + which distribution provided them
* discovered transport ids
* load failures (with traceback only in `--debug`, short error otherwise)

This is also where `plugins.enabled` / `plugins.auto_install` can matter later.

### Step 7: Decide semantics for `[plugins] enabled` and `auto_install`

You already have:

```py
class PluginsSettings(BaseModel):
    enabled: list[str] = ...
    auto_install: bool = False
```

You can make this meaningful without overcomplicating:

**Suggested semantics**

* `enabled = []` → load all installed plugins (default)
* `enabled = ["takopi-aider", "takopi-slack"]` → allowlist by distribution name; ignore other entrypoints
* `auto_install = true` (optional) → if config references unknown engine/transport, try installing a conventional package name (e.g. `takopi-engine-<id>` / `takopi-transport-<id>`) and re-run discovery

If you don’t want the complexity now: implement allowlisting first and leave auto-install as a later enhancement.

### Step 8: Tests for plugin discovery

Add tests that monkeypatch `importlib.metadata.entry_points()` to return synthetic entrypoints, and validate:

* IDs are listed without loading
* `get_backend()` loads and type-checks
* duplicate ids are rejected deterministically
* load failures are captured and don’t crash listing/help flows

---

## Extensibility beyond runners/transports: “slash commands” / directives

This can easily balloon your public API surface. I’d treat it as a second-stage feature.

### What I’d support (and what I’d postpone)

#### V1: “Directive macros” (safe-ish and composable)

Instead of arbitrary command handlers, support a simple extension point:

* a plugin can register a directive name (e.g. `review`)
* it transforms the incoming message into:

  * an engine override (optional)
  * a context override (optional)
  * a prompt prefix/suffix (optional)
  * whether to consume the directive token

This keeps the command system **purely declarative**: it doesn’t require plugins to hook into the Telegram loop or scheduler.

**Why this is good**

* transport-agnostic (works for any future Slack/Discord transport because it’s just text processing)
* low coupling: no need for plugins to call Telegram APIs
* safe-ish: less chance of plugins breaking invariants

**Implementation sketch**
Create a new core module `takopi/directives.py` and move parsing out of `telegram/bridge.py`.

Define a public Protocol:

```py
@dataclass(frozen=True, slots=True)
class DirectiveContext:
    engine_ids: tuple[str, ...]
    project_aliases: tuple[str, ...]  # or ProjectsConfig
    # maybe: config_path/settings for advanced cases (optional)

@dataclass(frozen=True, slots=True)
class DirectiveEffect:
    engine: str | None = None
    project: str | None = None
    branch: str | None = None
    prompt_prefix: str | None = None
    prompt_suffix: str | None = None

class DirectivePlugin(Protocol):
    name: str                 # "review"
    description: str          # for Telegram menu (optional)
    def apply(self, tokens: list[str], ctx: DirectiveContext) -> tuple[int, DirectiveEffect] | None: ...
```

Then core runs:

1. built-in directives (engine/project/branch)
2. plugin directives
3. remaining tokens become prompt

Entry point group:

```toml
[project.entry-points."takopi.directives"]
review = "takopi_review.directive:PLUGIN"
```

Telegram menu generation becomes:

* engines
* projects
* plugin directives (validated by regex + capped at 100)

#### V2: “Active commands” (do something without invoking an engine)

This is where complexity skyrockets:

* async handlers
* access to transport / reply message
* threading/scheduler integration
* permissions/security expectations

If you want this later, design it around a very explicit hook point in `runner_bridge.handle_message()` (e.g. “command resolved to a Response object” vs “invoke runner”), but I would not ship this in the first plugin version.

---

## What the public API should strive for

### Principles

1. **Small and explicit**: expose only what plugin authors need.
2. **Stable by default**: once you call it “plugin API v1”, don’t break it in minor releases.
3. **Transport-agnostic wherever possible**: runners and directives shouldn’t import Telegram.
4. **Pure data in, pure data out**: dataclasses/Protocols over “here’s a bunch of internal objects you can mutate”.
5. **Clear error boundary**: plugin failures should be contained and diagnosable.

### Concrete “public API surface” recommendation

Create a module like `takopi/api.py` that re-exports:

* `EngineBackend`, `EngineConfig`, `SetupIssue`
* `Transport`, `Presenter`, `TransportBackend`, `SetupResult`
* `Runner`, `BaseRunner`, `JsonlSubprocessRunner`, `EventFactory`
* `model` event types: `ResumeToken`, `StartedEvent`, `ActionEvent`, `CompletedEvent`, `Action`
* (if you do directives) `DirectivePlugin`, `DirectiveEffect`

…and document: “Anything not imported from `takopi.api` is internal and can change.”

Also add:

* `TAKOPI_PLUGIN_API_VERSION = 1`

### Compatibility policy

* Plugins should depend on `takopi>=0.11,<0.12` (or similar) initially.
* Once stable, you can widen ranges.
* If you change the plugin API, bump `TAKOPI_PLUGIN_API_VERSION` and maintain backward compatibility for at least one minor cycle (or provide a shim module).

---

## What to avoid

### Avoid in the plugin system design

* **Import-time execution** of plugin code for common commands (`--help`, `--version`, etc.).
* **Arbitrary “hook everything” callback registries** (hard to version, hard to reason about, creates spooky action at a distance).
* **Plugins depending on internal modules** like `takopi.telegram.bridge`, `scheduler`, `runner_bridge` internals. If a plugin needs them, either:

  * promote a small helper into `takopi.api`, or
  * keep it out of scope for plugins.

### Avoid in the plugin author UX

* Requiring plugin authors to subclass concrete classes that you might want to refactor.
* Forcing plugins to ship pydantic models that must integrate into your settings model. Dict-based config is fine; plugins can validate themselves.

### Avoid in identifiers / naming

* Allowing arbitrary engine ids that aren’t valid Telegram commands if you want `/engine` selection and command menu to remain consistent.
* Silent conflicts. If two plugins register the same engine id, fail loudly and point to both distributions.

---

## A practical “next version” scope I’d ship

If you want something that’s both useful and maintainable, aim for:

1. **Entrypoints for engines and transports** (core + plugins)
2. **Lazy loading** so help works even with broken plugins
3. **A small published plugin API module** (`takopi.api`)
4. **A `takopi plugins` introspection command**
5. (Optional) directives/macros as a separate step once discovery is solid

That gives you exactly what you asked for: people can add runners/transports outside core, and you’re not committing to an unbounded plugin surface area from day one.
