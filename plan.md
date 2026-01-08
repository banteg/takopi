Here’s a concrete, implementable refactor plan that (a) moves onboarding under “transport backends”, (b) keeps **transport settings** aligned with the current config model, and (c) introduces a **transport registry** that can later be extended by plugins — while keeping **Telegram built-in**.

I’m going to lay this out as a small sequence of commits that can land in a single PR without ripping up the whole runtime loop yet.

---

## Current config shape (already implemented)

The config shape lives in `src/takopi/settings.py` (`TakopiSettings`). It already enforces the TOML constraint that you **cannot** do:

```toml
transport = "telegram"

[transport.telegram]   # ❌ invalid: `transport` can’t be both scalar and table
```

So use a **plural table**:

```toml
default_engine = "codex"
transport = "telegram"

[transports.telegram]
bot_token = "123:ABC..."
chat_id = 123
```

Notes:

* **Legacy root keys are rejected.** `bot_token`/`chat_id` at the root raise a validation error; migration is required.
* Config parsing/validation is centralized in `TakopiSettings` + `require_telegram(...)`.
* When editing raw TOML, use `validate_settings_data(...)` on the merged dict.

---

## Commit 1 — (Already done) Transport settings are centralized in `TakopiSettings`

### Goal

Keep all validation and defaults in one place; avoid duplicating “parse telegram config” helpers.

### Current state

* `TakopiSettings` defines `transport` + `transports.telegram` and rejects legacy root keys.
* `require_telegram(...)` validates `transport == "telegram"` and required keys.
* `cli.py` uses `load_settings(...)` + `require_telegram(...)`.

### If you touch config parsing

* Do **not** add a parallel `transports_config.py` parser.
* Use `validate_settings_data(...)` when mutating raw dicts (e.g., onboarding/init).

---

## Commit 2 — Introduce a transport registry and wire CLI to it

### Goal

Make the CLI choose a transport backend by id, and delegate:

* config validation
* onboarding
* “run main loop”

…without forcing you to re-architect the bridge yet.

### New module: `src/takopi/transports.py`

Define a **backend interface** and a registry.

```py
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol

from .backends import EngineBackend
from .config import ConfigError
from .router import AutoRouter
from .config import ProjectsConfig
from .settings import TakopiSettings

@dataclass(frozen=True, slots=True)
class SetupResult:
    issues: list[Any]  # reuse SetupIssue
    config_path: Path
    @property
    def ok(self) -> bool: return not self.issues

class TransportBackend(Protocol):
    id: str
    description: str

    def check_setup(self, engine_backend: EngineBackend) -> SetupResult: ...
    def interactive_setup(self, *, force: bool) -> bool: ...

    def build_and_run(
        self,
        *,
        settings: TakopiSettings,
        config_path: Path,
        router: AutoRouter,
        projects: ProjectsConfig,
        final_notify: bool,
        default_engine_override: str | None,
    ) -> None: ...

_registry: dict[str, TransportBackend] = {}

def register_transport(backend: TransportBackend) -> None:
    _registry[backend.id] = backend

def get_transport(transport_id: str) -> TransportBackend:
    try:
        return _registry[transport_id]
    except KeyError:
        available = ", ".join(sorted(_registry))
        raise ConfigError(f"Unknown transport {transport_id!r}. Available: {available}.") from None

def list_transports() -> list[str]:
    return sorted(_registry)
```

### Implement Telegram backend wrapper: `src/takopi/telegram/backend.py`

This is a thin adapter around existing `telegram.onboarding` and `telegram.bridge.run_main_loop`.

* `check_setup()` can reuse your current `telegram.onboarding.check_setup` logic, but validate via `load_settings(...)` + `require_telegram(...)` (no legacy fallback).
* `interactive_setup()` calls `telegram.onboarding.interactive_setup(force=force)` (but PR 3 will change how it writes config).
* `build_and_run()` does what `_parse_bridge_config` + `anyio.run(run_main_loop, cfg)` currently do, but reads transport settings from `settings.transports.telegram` (or `require_telegram`).

Crucially: **move `_parse_bridge_config` out of `cli.py`** into this backend so CLI is transport-agnostic.

### Register telegram as built-in

In `takopi/transports.py` (or `takopi/__init__.py`), import and register telegram backend.

Example in `takopi/transports.py` bottom:

```py
from .telegram.backend import telegram_backend
register_transport(telegram_backend)
```

### CLI changes (`src/takopi/cli.py`)

1. Add option:

* `--transport` (string, optional) to:

  * app callback (`takopi`)
  * engine subcommands created by `make_engine_cmd`

2. Determine transport id:

* CLI flag overrides config
* else `settings.transport` (from `load_settings(...)`)
* else default `"telegram"`

3. Replace telegram-specific code paths:

* Replace imports of `TelegramBridgeConfig`, `TelegramClient`, `run_main_loop`, etc.
* Use `backend = transports.get_transport(transport_id)`
* Use `backend.check_setup(...)`
* Use `backend.interactive_setup(...)`
* Use `backend.build_and_run(...)`

At the end of PR 2:

* Telegram is still the only backend registered.
* But the CLI is now ready to run others.

### Tests

* Add `tests/test_transport_registry.py`:

  * list/get works
  * unknown transport error message includes available list

No onboarding test changes yet (PR 3 does that).

---

## Commit 3 — Refactor onboarding to write transport settings + preserve config

### Goal

Make `--onboard` (and “missing config -> wizard”) update only what it should:

* write to `[transports.telegram]`
* set `transport = "telegram"`
* set `default_engine`
* **do not wipe** `projects` or per-engine config tables

### Current behavior (already aligned)

* `interactive_setup(...)` reads existing TOML via `config_store.read_raw_toml`, merges changes, and writes via `write_raw_toml` (which uses `dump_toml`).
* It writes `transport = "telegram"` and `[transports.telegram]` with `bot_token`/`chat_id`, and removes legacy root keys.
* Preview still uses `_render_config(...)` with a masked token (fine to keep).

### Remaining UX fix

* If config exists but is invalid, the auto-flow still calls `interactive_setup(force=False)`, which prints “config exists…use --onboard”.
* Update the CLI flow (after PR 2) to prompt: “Config is missing/invalid for telegram. Run onboarding now? (y/N)” and run `interactive_setup(force=True)` on yes.

### Tests to update

Update:

* `tests/test_onboarding.py`
* `tests/test_onboarding_interactive.py`

Concrete assertions after the change:

* saved config contains:

  * `transport = "telegram"`
  * `[transports.telegram]` with bot_token/chat_id
  * `default_engine = "codex"` (if selected)
* does **not** erase existing `[projects]` if present (add a new test!)

Example new test:

* seed config file with `[projects.foo]...`
* run onboarding
* assert projects still exist in saved TOML dict (load it with `tomllib.loads`)

---

## Commit 4 — Polish: add “list transports” + tidy errors

Not strictly required, but keeps things clean.

1. Add CLI command:

* `takopi transports` (or `--list-transports`) to print available transport ids.
  This will matter as soon as you add plugin discovery.

2. Keep the legacy-key failure explicit.

* The model validator already rejects root `bot_token/chat_id`. Optionally tweak the error to point at `[transports.telegram]`.

---

## Where this leaves you

After commits 2–3 (commit 1 is already true in current code), you’ll have:

* a config format that scales to N transports
* onboarding that doesn’t destroy unrelated config
* a transport registry with Telegram wired in
* CLI that can select a transport via `--transport` or config `transport`

…and you still haven’t had to rewrite the bridge loop. Telegram remains the only built-in backend; later you can let plugins register additional `TransportBackend`s.

If you want, I can also propose the exact diffs for `cli.py` (what moves into `telegram/backend.py`, what stays core), but the above is the concrete path that keeps each PR reviewable and low-risk.
