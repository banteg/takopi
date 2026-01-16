## `takopi config` CLI subcommand spec (lean)

### Goal

Add a new CLI command group:

* `takopi config …`

that lets users **read and modify** `~/.takopi/takopi.toml` (the *single* Takopi config file) without manually editing TOML. It should feel “`git config`-like”, but optimized for the **90% use case**: strings, booleans, and simple numbers.

Non-goals:

* Supporting multiple config scopes/files (no `--global`/`--system`).
* Preserving comments/formatting perfectly (acceptable to rewrite the file).
* Editing runtime-only state (sessions/prefs stored elsewhere).
* Edge-case key syntax (quoted segments, escaped dots).
* Array mutation helpers (`add`/`remove`).
* A standalone `validate` command.

---

## Command surface

### Top-level

`takopi config` is a **command group** with these subcommands:

* `takopi config path`
* `takopi config list`
* `takopi config get`
* `takopi config set`
* `takopi config unset`

All subcommands accept a common option:

* `--config-path PATH`
  Overrides the default config path (`~/.takopi/takopi.toml`). Intended for testing, CI, or advanced setups.

---

## Key path syntax

Takopi config keys are hierarchical. The CLI uses a **simple dot-path** syntax:

* `default_engine`
* `transports.telegram.chat_id`
* `projects.happy-gadgets.path`
* `plugins.enabled`

Rules:

* A key path is one or more **bare** segments separated by `.`.
* Segment characters: `[A-Za-z0-9_-]+` only.
* No escaping, no quoted segments.

If a user needs dots inside a key segment, they should edit the TOML file directly.

### Table creation rules

When setting/unsetting values:

* Missing intermediate tables **MUST** be created (as dicts).
* If an intermediate segment exists but is **not** a table (dict), the command **MUST** fail with a clear error:

  * “cannot set `a.b.c`: `a.b` is not a table”

### Case normalization

* Keys are treated as **case-sensitive**.

---

## Value parsing (the “smart guess”)

`takopi config set` uses a single heuristic and no type flags:

1. Try to parse the value as a **TOML literal** (e.g., `true`, `123`, `1.5`, `["a", "b"]`, `{ enabled = true }`).
2. If parsing fails or the input is a bare word, treat it as a **string**.

Practical parsing rules:

* `true` / `false` -> Boolean
* `123` / `-5` -> Integer
* `1.5` -> Float
* Starts with `[` or `{` -> attempt TOML array/table parse
* Otherwise -> String

---

## Subcommand specs

### 1) `takopi config path`

Print the resolved config path.

**Usage**

```sh
takopi config path
takopi config path --config-path ./takopi.toml
```

**Output**

* Print a single line path (`~`-relative if possible).

**Exit codes**

* `0` always (unless an OS error occurs formatting/expanding the path).

---

### 2) `takopi config list`

List config as flattened dot-keys.

**Usage**

```sh
takopi config list
```

**Output**

* One key per line: `key = value`
* Values printed as TOML literals (strings quoted) so output can be reused.
* Output is **raw TOML only** (no default values injected by Pydantic).

Example:

```
default_engine = "codex"
transports.telegram.chat_id = -100123456
transports.telegram.bot_token = "123456:ABCDEF..."
```

**Exit codes**

* `0` if config file exists and is parseable TOML.
* `1` if config file missing.
* `2` if TOML malformed or unreadable.

---

### 3) `takopi config get <key>`

Fetch a single key.

**Usage**

```sh
takopi config get default_engine
```

**Behavior**

* If key exists and is a leaf value: print it as a TOML literal.
* If key exists and is a table: error with a hint ("this is a table; pick a leaf node").
* If key does not exist: print nothing.
* Uses raw TOML data only (no default values injected).

**Exit codes**

* `0` found
* `1` not found
* `2` malformed TOML / config read error / key is a table

---

### 4) `takopi config set <key> <value>`

Set a key to a value.

**Usage**

```sh
takopi config set default_engine "openai"
takopi config set transports.telegram.chat_id 12345
takopi config set transports.telegram.topics.enabled true
takopi config set plugins.enabled '["plugin-a", "plugin-b"]'
```

**Behavior**

1. Load current TOML.
2. Navigate to the path. Create intermediate tables if missing.
3. Parse `<value>` with the smart-guess rules.
4. Validate the updated config against the config schema.
5. Write the file atomically.
6. Print a confirmation line, e.g.:

   * `updated default_engine = "openai"`

**Exit codes**

* `0` success
* `2` parse error / invalid key path / schema validation error / write error

---

### 5) `takopi config unset <key>`

Remove a key.

**Usage**

```sh
takopi config unset default_project
takopi config unset projects.old-project
```

**Behavior**

* Remove the leaf key from its parent table.
* Prune empty tables up the path after removal.
* Validate the updated config against the config schema before writing.

**Exit codes**

* `0` key existed and was removed
* `1` key not found (no change)
* `2` invalid path / config read error / schema validation error / write error

---

## Validation for mutations

For `set` and `unset`, validate the updated config against the **config schema** before writing
(TOML-only, no env overlays):

* If schema validation fails, abort and **do not write**.
* This is strict: if the existing config is incomplete (e.g., missing required tables),
  `set`/`unset` will fail until the config is made valid.

No separate `validate` command and no validation modes.

Notes:

* Validation should use the existing helper in `src/takopi/settings.py`
  (`validate_settings_data(data, config_path=...)`) so errors surface as `ConfigError`.
* This enforces `ProjectSettings`’s strictness (extra=forbid) while still allowing
  unknown **top-level** tables via `TakopiSettings` (extra=allow).

---

## Atomic write requirement

All mutation commands MUST write config **atomically** to avoid partial writes that can break hot-reload (`watch_config`):

* Write TOML to `takopi.toml.tmp` (or similar) in the same directory.
* `os.replace(tmp, config_path)` to commit.

This ensures:

* readers never observe a half-written file
* `watchfiles` reloads see a consistent file

---

## Migrations

Before any read-modify-write operation:

* Config migrations SHOULD be applied to the in-memory config first (same behavior as startup).

If migrations result in changes, they can be persisted as part of the same atomic write.

---

## Examples (lean UX)

```sh
# Discovery
takopi config path
# /home/user/.takopi/takopi.toml

# Simple set
takopi config set default_engine "openai"

# Nested paths work automatically
takopi config set transports.telegram.chat_id 12345

# Lists still work if passed as a string
takopi config set plugins.enabled '["plugin-a", "plugin-b"]'

# Removal
takopi config unset projects.old-project
```

---

## Implementation notes aligned with the current codebase

* Add `"config"` to `RESERVED_CLI_COMMANDS` so no engine plugin can register id `config`.
* Reuse `load_or_init_config()` and `write_config()` (but update/augment to support atomic write).
* Reuse `migrate_config()`/`migrate_config_file()` logic, but prefer “migrate in-memory then write once” for config edits.
* For `list`, implement a deterministic flatten that walks dicts and emits dot-path keys.
* Use `validate_settings_data()` rather than `load_settings()` so CLI validation is TOML-only
  (no env overrides) and returns consistent `ConfigError` messages.
