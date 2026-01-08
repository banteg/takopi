Here’s a concrete, implementable refactor plan that (a) moves onboarding under “transport backends”, (b) adds **transport settings** to the config cleanly, and (c) introduces a **transport registry** that can later be extended by plugins — while keeping **Telegram built-in**.

I’m going to lay this out as a small sequence of PRs you can land independently without ripping up the whole runtime loop yet.

---

## First: decide the config shape (important TOML constraint)

You **cannot** do:

```toml
transport = "telegram"

[transport.telegram]   # ❌ invalid: `transport` can’t be both scalar and table
```

So use **plural table**:

```toml
default_engine = "codex"
transport = "telegram"

[transports.telegram]
bot_token = "123:ABC..."
chat_id = 123
```

Back-compat rule for v0.10/v0.11:

* If `[transports.telegram]` is missing, fall back to legacy root keys:

  * `bot_token`
  * `chat_id`

On write (onboarding), write the new layout and optionally remove the legacy keys.

---

## PR 1 — Add “transport settings” parsing (no registry yet)

### Goal

Centralize “read config → validate transport settings” so CLI and onboarding stop duplicating parsing.

### Changes

1. **New module** `src/takopi/transports_config.py` (or `transports/config.py` if you prefer a package)

Add helpers:

```py
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import ConfigError

@dataclass(frozen=True, slots=True)
class TelegramSettings:
    bot_token: str
    chat_id: int

def resolve_transport_id(config: dict[str, Any], config_path: Path) -> str:
    value = config.get("transport") or "telegram"
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"Invalid `transport` in {config_path}; expected a non-empty string.")
    return value.strip()

def _get_transports_table(config: dict[str, Any], config_path: Path) -> dict[str, Any]:
    tbl = config.get("transports") or {}
    if not isinstance(tbl, dict):
        raise ConfigError(f"Invalid `transports` in {config_path}; expected a table.")
    return tbl

def parse_telegram_settings(config: dict[str, Any], config_path: Path) -> TelegramSettings:
    transports = _get_transports_table(config, config_path)
    telegram = transports.get("telegram")
    if isinstance(telegram, dict):
        token = telegram.get("bot_token")
        chat_id = telegram.get("chat_id")
    else:
        # legacy fallback
        token = config.get("bot_token")
        chat_id = config.get("chat_id")

    if not isinstance(token, str) or not token.strip():
        raise ConfigError(f"Missing/invalid Telegram bot token in {config_path} (transports.telegram.bot_token).")
    if isinstance(chat_id, bool) or not isinstance(chat_id, int):
        raise ConfigError(f"Missing/invalid Telegram chat_id in {config_path} (transports.telegram.chat_id).")

    return TelegramSettings(bot_token=token.strip(), chat_id=chat_id)
```

2. **Remove duplication**: stop using `src/takopi/telegram/config.py` for reads.

* CLI should use `load_or_init_config()` + “raise if empty/missing required keys”.
* Keep `telegram/config.py` temporarily if you want, but it’s redundant once settings parsing is centralized.

3. Update `cli.py`:

* Replace `load_and_validate_config()` with:

  * `config, config_path = load_or_init_config()` (but error if `{}` returned)
  * `settings = parse_telegram_settings(config, config_path)`

### Tests to add/update

* New unit tests in e.g. `tests/test_transport_settings.py`:

  * parses new layout
  * parses legacy layout
  * errors are good
* Update existing onboarding tests later in PR 3.

This PR is mostly “plumbing + safety”, no behavior change yet besides allowing the new layout.

---

## PR 2 — Introduce a transport registry and wire CLI to it

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
        config: dict[str, Any],
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

* `check_setup()` can reuse your current `telegram.onboarding.check_setup` logic, but update it to look for the new config layout (or call `parse_telegram_settings()` and catch errors).
* `interactive_setup()` calls `telegram.onboarding.interactive_setup(force=force)` (but PR 3 will change how it writes config).
* `build_and_run()` does what `_parse_bridge_config` + `anyio.run(run_main_loop, cfg)` currently do.

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
* else config `transport`
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

## PR 3 — Refactor onboarding to write transport settings + preserve config

### Goal

Make `--onboard` (and “missing config -> wizard”) update only what it should:

* write to `[transports.telegram]`
* set `transport = "telegram"`
* set `default_engine`
* **do not wipe** `projects` or per-engine config tables

### How to implement (concretely)

#### 1) Stop writing raw TOML strings in `telegram/onboarding.py`

Right now you do `_render_config(...)` and `config_path.write_text(...)` which overwrites everything.

Instead:

* load existing config dict
* mutate it
* write via `takopi.config.write_config()` (already exists!)

Proposed structure:

```py
from ..config import load_or_init_config, write_config, dump_toml

def _ensure_table(config: dict, key: str, config_path: Path) -> dict:
    tbl = config.get(key)
    if tbl is None:
        tbl = {}
        config[key] = tbl
    if not isinstance(tbl, dict):
        raise ConfigError(f"Invalid `{key}` in {config_path}; expected a table.")
    return tbl

def _update_telegram_transport_config(config: dict, config_path: Path, *, token: str, chat_id: int) -> None:
    transports = _ensure_table(config, "transports", config_path)
    telegram = transports.get("telegram")
    if telegram is None:
        telegram = {}
        transports["telegram"] = telegram
    if not isinstance(telegram, dict):
        raise ConfigError(f"Invalid `transports.telegram` in {config_path}; expected a table.")
    telegram["bot_token"] = token
    telegram["chat_id"] = chat_id

    # mark selected transport if missing (or always set during telegram onboarding)
    config["transport"] = "telegram"

    # optional cleanup of legacy keys:
    config.pop("bot_token", None)
    config.pop("chat_id", None)
```

Then when user confirms:

```py
config, config_path = load_or_init_config()
_update_telegram_transport_config(config, config_path, token=token, chat_id=chat_id)
if default_engine: config["default_engine"] = default_engine
write_config(config, config_path)
```

#### 2) Preview: use `dump_toml()` for display

To keep masking, generate a “preview copy”:

```py
preview = copy.deepcopy(config)
preview["transports"]["telegram"]["bot_token"] = _mask_token(token)
console.print(dump_toml(preview))
```

#### 3) Fix the “config exists but invalid” UX

Currently, if config exists but is invalid, the auto-flow tries `interactive_setup(force=False)` which just says “config exists…use --onboard”.

Update logic in `cli.py` after PR 2:

* if transport backend check says “missing/invalid config” and TTY:

  * prompt: “Config is missing/invalid for telegram. Run onboarding now? (y/N)”
  * if yes, run `interactive_setup(force=True)`.

That will dramatically reduce “why is it failing” friction.

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

## PR 4 — Polish: remove old telegram config loader + add “list transports”

Not strictly required, but keeps things clean.

1. Remove `src/takopi/telegram/config.py`

* Everything should go through `takopi.config` + `parse_telegram_settings`.

2. Add CLI command:

* `takopi transports` (or `--list-transports`) to print available transport ids.
  This will matter as soon as you add plugin discovery.

3. Add a deprecation warning for legacy `bot_token/chat_id` at root

* only log once at startup (or only when parsing legacy format).

---

## Where this leaves you

After PR 1–3, you’ll have:

* a config format that scales to N transports
* onboarding that doesn’t destroy unrelated config
* a transport registry with Telegram wired in
* CLI that can select a transport via `--transport` or config `transport`

…and you still haven’t had to rewrite the bridge loop. Telegram remains the only built-in backend; later you can let plugins register additional `TransportBackend`s.

If you want, I can also propose the exact diffs for `cli.py` (what moves into `telegram/backend.py`, what stays core), but the above is the concrete path that keeps each PR reviewable and low-risk.
