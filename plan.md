Here’s a concrete, *doable* migration plan to move Takopi’s config to **`pydantic_settings`** without blowing up the codebase, while also setting you up for **plugins + multi-transport onboarding**.

## First: version constraints (important with your `>=3.14`)

Because Takopi targets **Python ≥ 3.14**, you should **require Pydantic v2.12+** (that’s where initial Python 3.14 support landed). ([Pydantic][1])
Pydantic 2.9.x only added support up to Python 3.13. ([PyPI][2])
`pydantic-settings` 2.12.0 is current and pairs naturally with that. ([PyPI][3])

So in Takopi’s `pyproject.toml`, plan on:

* `pydantic>=2.12`
* `pydantic-settings>=2.12`

## Target end-state (what “done” looks like)

* One canonical **`TakopiSettings`** object loaded from:

  1. CLI init kwargs (highest priority)
  2. environment variables (e.g. `TAKOPI__TRANSPORT=telegram`)
  3. `~/.takopi/takopi.toml` (lowest priority)
* Settings are **typed**, **nested**, **validated** with good errors.
* Unknown sections (engine configs like `[codex]`, plugin configs, future keys) are **preserved** (so you don’t block growth).
* Onboarding writes config **without clobbering unrelated sections** (fixes the “onboarding overwrites projects” issue).

---

# Plan (split into 4 PRs)

## PR 1 — Introduce `TakopiSettings` + TOML source + compat shim

**Goal:** Add pydantic-settings without changing runtime behavior yet.

### 1) Add deps

Update `pyproject.toml`:

* `pydantic>=2.12`
* `pydantic-settings>=2.12`

Regenerate `uv.lock`.

### 2) Add new settings module

Create `src/takopi/settings.py` (or `src/takopi/settings/__init__.py` if you prefer a package).

It should define:

* `HOME_CONFIG_PATH` (single source of truth; remove duplication in telegram/config later)
* `ConfigError` stays (you already have it; keep it as your UX layer)
* Typed models for the “stable” config surface:

  * default_engine
  * projects + default_project
  * transport selection + telegram credentials
  * plugins section (even if it’s unused yet, it prevents another migration later)

### 3) Add TOML as a pydantic-settings source

**Key gotcha:** `pydantic_settings` will *not* read TOML unless you add `TomlConfigSettingsSource` in `settings_customise_sources`.

Also: source ordering matters. In pydantic-settings, **earlier sources win** (higher priority). So do `env` before `toml` if env should override file.

Skeleton (illustrative, not final):

```py
# src/takopi/settings.py
from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_serializer, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic_settings.sources import TomlConfigSettingsSource

HOME_CONFIG_PATH = Path.home() / ".takopi" / "takopi.toml"

class TelegramTransportSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bot_token: SecretStr | None = None
    chat_id: int | None = None

    # critical: let us write secrets back to TOML
    @field_serializer("bot_token")
    def _dump_token(self, v: SecretStr | None) -> str | None:
        return v.get_secret_value() if v else None

class TransportsSettings(BaseModel):
    # keep telegram typed
    telegram: TelegramTransportSettings = Field(default_factory=TelegramTransportSettings)

    # allow plugin-defined transport blocks like [transports.discord]
    model_config = ConfigDict(extra="allow")

class PluginsSettings(BaseModel):
    enabled: list[str] = Field(default_factory=list)
    auto_install: bool = False
    model_config = ConfigDict(extra="allow")  # plugin-specific tables

class TakopiSettings(BaseSettings):
    model_config = SettingsConfigDict(
        extra="allow",                 # preserve [codex], [claude], etc
        env_prefix="TAKOPI__",
        env_nested_delimiter="__",
        # toml_file gets injected via the loader (see below)
    )

    default_engine: str = "codex"
    default_project: str | None = None
    projects: dict[str, dict[str, Any]] = Field(default_factory=dict)

    transport: str = "telegram"
    transports: TransportsSettings = Field(default_factory=TransportsSettings)

    plugins: PluginsSettings = Field(default_factory=PluginsSettings)

    @model_validator(mode="before")
    @classmethod
    def _legacy_telegram_keys(cls, data: Any) -> Any:
        # Support v0.9.0 configs that have top-level bot_token/chat_id
        if isinstance(data, dict) and ("bot_token" in data or "chat_id" in data):
            transports = data.setdefault("transports", {})
            telegram = transports.setdefault("telegram", {})
            if "bot_token" in data:
                telegram.setdefault("bot_token", data.pop("bot_token"))
            if "chat_id" in data:
                telegram.setdefault("chat_id", data.pop("chat_id"))
            data.setdefault("transport", "telegram")
        return data

    @classmethod
    def settings_customise_sources(cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings):
        # priority: init > env > dotenv > toml > secrets
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            TomlConfigSettingsSource(settings_cls),
            file_secret_settings,
        )

def load_settings(path: Path | None = None) -> tuple[TakopiSettings, Path]:
    cfg_path = path or HOME_CONFIG_PATH

    # avoid global mutation: create a subclass with toml_file bound
    cfg = dict(TakopiSettings.model_config)
    cfg["toml_file"] = str(cfg_path)
    Bound = type("TakopiSettingsBound", (TakopiSettings,), {"model_config": SettingsConfigDict(**cfg)})

    try:
        return Bound(), cfg_path
    except Exception as e:
        # convert ValidationError -> ConfigError with nicer message
        raise ConfigError(str(e)) from e
```

**Decisions embedded here that you want:**

* Keep engine configs (e.g. `[codex]`) as “extras” so you don’t have to model them immediately.
* Put transports under `[transports.<id>]` while keeping `transport = "<id>"` (avoids TOML key/table conflicts).
* Migrate legacy `bot_token/chat_id` automatically.

### 4) Add tests just for settings correctness

Add `tests/test_settings.py` (new):

* loads from TOML
* env overrides TOML
* legacy top-level keys become `transports.telegram.*`
* unknown top-level tables are preserved (extras)

No production code changes yet besides adding settings module.

---

## PR 2 — Wire CLI runtime reads to `TakopiSettings` (read path only)

**Goal:** Replace *manual validation* in `cli.py` and remove `telegram/config.py` duplication.

### 1) Replace `load_and_validate_config()` in `cli.py`

Currently it:

* reads dict
* validates bot_token/chat_id manually

New flow:

* `settings, cfg_path = load_settings()`
* enforce “telegram ready” (still your ConfigError style)

Add helper in settings module:

```py
def require_telegram(settings: TakopiSettings, cfg_path: Path) -> tuple[str, int]:
    if settings.transport != "telegram":
        raise ConfigError(f"Unsupported transport {settings.transport!r} (telegram only for now).")

    tg = settings.transports.telegram
    if tg.bot_token is None or not tg.bot_token.get_secret_value().strip():
        raise ConfigError(f"Missing bot token in {cfg_path}.")
    if tg.chat_id is None:
        raise ConfigError(f"Missing chat_id in {cfg_path}.")

    return tg.bot_token.get_secret_value().strip(), tg.chat_id
```

Then CLI uses:

* token/chat_id from the settings model
* `default_engine` from settings model
* projects still parsed via your existing `parse_projects_config` for now (or temporarily use `settings.model_dump()` to pass into it)

### 2) Stop calling `load_telegram_config`

* Delete `src/takopi/telegram/config.py` and update imports.
* Update `telegram/onboarding.py` “check existing config” code to call `load_settings()` or to check file existence directly.

### 3) Update tests that monkeypatch `load_telegram_config`

* `tests/test_onboarding.py` will need to monkeypatch `load_settings` (or provide a temp config file and let settings load it).

At the end of PR2, Takopi still behaves the same, but config IO is centralized and typed.

---

## PR 3 — Convert Projects section to typed models + keep existing runtime `ProjectsConfig`

**Goal:** Eliminate `parse_projects_config()`’s manual validation over time, but *keep* the existing `ProjectsConfig` dataclass so you don’t have to rewrite bridge/worktrees in one shot.

### 1) Define `ProjectSettings` (typed) and `ProjectsSettings` (typed)

In `settings.py`:

```py
class ProjectSettings(BaseModel):
    path: str
    worktrees_dir: str = ".worktrees"
    default_engine: str | None = None
    worktree_base: str | None = None
    model_config = ConfigDict(extra="forbid")
```

Then in `TakopiSettings`:

* `projects: dict[str, ProjectSettings] = Field(default_factory=dict)`
* `default_project: str | None = None`

### 2) Move *validation rules* to a conversion layer

You have validations that depend on discovered engines + reserved words. That’s not a great fit for pure field validators because it needs runtime context.

So add a method:

```py
def to_projects_config(
    self,
    *,
    config_path: Path,
    engine_ids: list[str],
    reserved: tuple[str, ...] = ("cancel",),
) -> ProjectsConfig:
    # implement the same alias rules you have now
    # normalize project path relative to config_path.parent (keep your current behavior)
```

This method returns the existing runtime `ProjectsConfig` / `ProjectConfig` dataclasses that `telegram/bridge.py` and `worktrees.py` already use.

### 3) Replace `parse_projects_config()` calls

In `cli.py`, replace:

* `projects = parse_projects_config(config, ...)`

with:

* `projects = settings.to_projects_config(config_path=cfg_path, engine_ids=[...], reserved=(...))`

### 4) Update tests

* Replace tests that import `parse_projects_config` with tests for `TakopiSettings.to_projects_config()`.
* Keep the `cli init` test, but update its assertions if you’ve changed how config is written (see PR4).

At end of PR3:

* All project config is typed (path, defaults, etc)
* Alias/engine collision rules still exist
* Runtime bridge code stays untouched

---

## PR 4 — Switch onboarding + config writing to “merge/update” mode

**Goal:** Make onboarding safe with big configs and not delete `[projects.*]`, `[plugins]`, engine sections, etc.

This is where pydantic-settings pays off immediately.

### 1) Create a small config store helper (raw TOML merge)

Even with pydantic-settings, you want a safe way to *write* without accidentally dropping unknown keys.

Add `src/takopi/config_store.py` (or repurpose current `config.py` down to IO only):

* `read_raw_toml(path) -> dict`
* `write_raw_toml(path, dict)`

Keep your existing `dump_toml()` (it’s fine), but extend `_format_toml_value` to support `Path` if needed (or ensure you always stringify before writing).

### 2) Onboarding should:

* load existing raw dict if present
* update only:

  * `default_engine`
  * `transport` selection
  * `transports.telegram.bot_token`
  * `transports.telegram.chat_id`
* write merged dict back

This fixes the current “wizard overwrites config (including projects)” issue.

### 3) Onboarding should write the *new* transport shape

Write:

```toml
transport = "telegram"

[transports.telegram]
bot_token = "..."
chat_id = 123
```

But keep the legacy read-path (PR1’s migration) so old configs continue to work.

### 4) Update onboarding checks

* `check_setup()` should become:

  * “is config file present OR can settings load required keys”
  * “does selected engine exist in PATH”
  * “are telegram credentials present”

### 5) Update tests

* Add regression test: existing config with `[projects.*]` survives onboarding write.
* Update interactive onboarding tests if they assert the exact saved TOML.

---

# Optional follow-ups (nice wins once the basics land)

## Convert engine configs gradually (per-runner)

Right now each runner does manual `config.get()` + type checks.

After PR2, you can convert each engine section to a pydantic model **without touching the global settings**:

* Define `CodexRunnerSettings(BaseModel)` inside `runners/codex.py`
* In `build_runner`, do:

  * `cfg = CodexRunnerSettings.model_validate(settings.engine_config("codex"))`
* Then runner code reads `cfg.extra_args` etc.

Do this one engine at a time; the API surface is contained.

## Add a `takopi config check` command

When settings get large, a “doctor” command that prints:

* missing required keys for chosen transport
* engines available/missing
* invalid project aliases
  is *very* useful.

---

# Summary checklist

If you want the “short list” of what needs to happen:

1. Add deps (`pydantic>=2.12`, `pydantic-settings>=2.12`) ([Pydantic][1])
2. Add `TakopiSettings` using `TomlConfigSettingsSource` + env precedence
3. Add legacy key migration (`bot_token/chat_id` → `transports.telegram.*`)
4. Wire CLI reads to settings + remove duplicate telegram config reader
5. Migrate projects parsing to typed model + conversion to existing `ProjectsConfig`
6. Rework onboarding to merge-write config (no clobber) + write new transport structure
7. Update tests + add merge regression tests

If you want, I can turn this into a concrete set of file-level diffs for PR1 (settings module + loader + tests) based on your current tree so you can start landing it immediately.

[1]: https://pydantic.dev/articles/pydantic-v2-12-release?utm_source=chatgpt.com "Announcement: Pydantic v2.12 Release"
[2]: https://pypi.org/project/pydantic/2.9.2/?utm_source=chatgpt.com "pydantic 2.9.2"
[3]: https://pypi.org/project/pydantic-settings/?utm_source=chatgpt.com "pydantic-settings"
