## `takopi config` CLI subcommand spec

### Goal

Add a new CLI subcommand:

* `takopi config …`

that lets users **read and modify** `~/.takopi/takopi.toml` (the *single* Takopi config file) without manually editing TOML. It should feel “`git config`-like”, but adapted to **nested TOML keys** and Takopi’s config schema.

Non-goals:

* Supporting multiple config scopes/files (no `--global`/`--system`).
* Preserving comments/formatting perfectly (acceptable to rewrite the file).
* Editing runtime-only state (sessions/prefs stored elsewhere).

---

## Command surface

### Top-level

`takopi config` is a **command group** with these subcommands:

* `takopi config path`
* `takopi config list`
* `takopi config get`
* `takopi config set`
* `takopi config unset`
* `takopi config add`
* `takopi config remove`
* `takopi config validate`

All subcommands accept a common option:

* `--config-path PATH`
  Overrides the default config path (`~/.takopi/takopi.toml`). Intended for testing, CI, or advanced setups.

---

## Key path syntax

Takopi config keys are hierarchical. The CLI uses a **dot-path** grammar:

* `default_engine`
* `transports.telegram.chat_id`
* `transports.telegram.topics.enabled`
* `projects.happy-gadgets.path`
* `plugins.enabled`
* `codex.model` (engine-specific tables are allowed)

### Grammar

* A **KeyPath** is one or more segments separated by `.`.
* Each segment can be:

  * **bare**: `[A-Za-z0-9_-]+`
  * **quoted**: `"…"` (TOML-style quoted key segment; supports escaping)

Examples:

* `projects.happy-gadgets.path`
* `plugins."takopi-engine-acme".foo`

### Table creation rules

When setting/adding/removing values:

* Missing intermediate tables **MUST** be created (as dicts).
* If an intermediate segment exists but is **not** a table (dict), the command **MUST** fail with a clear error:

  * “cannot set `a.b.c`: `a.b` is not a table”

### Case normalization

* Keys are treated as **case-sensitive** by default.
* **Special case**: `projects.<alias>` SHOULD match an existing project alias case-insensitively when possible (to avoid creating duplicates like `Happy-Gadgets` vs `happy-gadgets`).

---

## Value parsing

`takopi config` needs to set typed TOML values (bool/int/float/arrays/tables), but also be friendly for common string values.

### Parse modes

`takopi config set/add/remove` accept:

* `--type auto` *(default)*
  Try TOML-literal parsing first; if it fails, treat the input as a string.
* `--type toml`
  Require valid TOML literal; fail if parse fails.
* `--type string`
  Always store as a string (no TOML parsing).
* `--type json`
  Parse JSON and store as the corresponding TOML value (objects become inline tables where possible).

### TOML-literal parsing (for `auto` / `toml`)

Implementation MUST parse user input as a TOML value using a wrapper technique equivalent to:

* parse `__v__ = <user_value>` with `tomllib`
* extract `__v__`

This enables:

* `true`, `false`
* `123`, `-100123`
* `1.5`
* `["a", "b"]`
* `{ enabled = true }`

### Complex values via stdin

For multi-line or complex values, support:

* `--stdin` (or allow value argument `-`)

Examples:

```sh
printf '[1, 2, 3]\n' | takopi config set transports.telegram.files.allowed_user_ids - --type toml --stdin
```

Rules:

* If `--stdin` is set, the `<value>` positional MAY be omitted.
* If both are provided, stdin wins (simpler mental model).

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

* Print a single line path (user-friendly, `~`-relative if possible).

**Exit codes**

* `0` always (unless an OS error occurs formatting/expanding the path).

---

### 2) `takopi config list`

List config as flattened dot-keys.

**Usage**

```sh
takopi config list
takopi config list --name-only
takopi config list --json
takopi config list --show-secrets
```

**Options**

* `--name-only`: print keys only, one per line.
* `--json`: emit a JSON object where keys are dot-paths and values are JSON-serializable.
* `--show-secrets`: don’t redact secrets (default is redacted).

**Default output format (text)**

* One key per line: `key = value`
* Values printed as TOML literals (strings quoted) so output can be reused.

Example:

```
default_engine = "codex"
transports.telegram.chat_id = -100123456
transports.telegram.bot_token = "********…"
```

**Redaction**
By default, redact values for known sensitive keys (at minimum):

* `transports.telegram.bot_token`

(Optionally extend with heuristics: segment names containing `token`, `secret`, `password`, `api_key`.)

**Exit codes**

* `0` if config file exists and is parseable TOML.
* `1` if config file missing.
* `2` if TOML malformed or unreadable.

---

### 3) `takopi config get`

Fetch a single key.

**Usage**

```sh
takopi config get default_engine
takopi config get transports.telegram.chat_id
takopi config get transports.telegram.bot_token --show-secrets
takopi config get plugins.enabled --json
```

**Options**

* `--json`: print JSON for the value only.
* `--toml`: print a TOML literal for the value only (strings quoted).
* `--raw`: for strings, print without quotes (git-config-like); for non-strings, behaves like `--toml`.
* `--default VALUE`: if key is missing, print VALUE and exit `0`.
* `--show-secrets`: allow showing secret values (otherwise redact).

**Behavior**

* If the key exists:

  * Print it in the chosen format.
  * Exit `0`.
* If the key does not exist:

  * If `--default` provided: print default and exit `0`.
  * Else: print nothing and exit `1`.

**Exit codes**

* `0` found (or default used)
* `1` not found
* `2` malformed TOML / config read error

---

### 4) `takopi config set`

Set a key to a value.

**Usage**

```sh
takopi config set default_engine claude
takopi config set transports.telegram.chat_id -100123
takopi config set transports.telegram.bot_token "123:ABCDEF"
takopi config set transports.telegram.topics.enabled true
takopi config set plugins.enabled '["takopi-engine-acme"]' --type toml
```

**Options**

* `--type {auto,toml,string,json}`
* `--stdin`
* `--no-prune`: don’t remove empty tables after edits (default is prune)
* `--validate {auto,always,never}` *(see validation section)*

**Behavior**

* Creates intermediate tables as needed.
* Replaces the value at the leaf key.
* Prints a confirmation line (redacted for secrets), e.g.:

  * `updated default_engine = "claude"`
* Writes config to disk.

**Exit codes**

* `0` success
* `2` parse error / type mismatch / invalid key path / write error

---

### 5) `takopi config unset`

Remove a key.

**Usage**

```sh
takopi config unset default_project
takopi config unset transports.telegram.topics
takopi config unset projects.happy-gadgets.chat_id
```

**Behavior**

* Removes the leaf key from its parent table.
* If `--prune` (default), recursively removes now-empty parent tables.

**Exit codes**

* `0` key existed and was removed
* `1` key not found (no change)
* `2` invalid path / config read error / write error

---

### 6) `takopi config add`

Append a value to a TOML array key (create array if missing).

**Usage**

```sh
takopi config add plugins.enabled takopi-engine-acme
takopi config add transports.telegram.files.allowed_user_ids 123 --type toml
```

**Options**

* `--unique/--no-unique` (default `--unique`)
* `--type {auto,toml,string,json}`
* `--stdin` (for complex values)

**Behavior**

* If key is missing: create an array with the new value.
* If key exists:

  * If it’s not an array: error.
  * Else append (or skip if `--unique` and already present).
* Print a confirmation line.

**Exit codes**

* `0` success (including “already present” when `--unique`)
* `2` type/path error or write error

---

### 7) `takopi config remove`

Remove a value from a TOML array key.

**Usage**

```sh
takopi config remove plugins.enabled takopi-engine-acme
takopi config remove transports.telegram.files.allowed_user_ids 123 --type toml
```

**Options**

* `--all/--first` (default `--all`)
  Remove all matches or only the first match.
* `--type {auto,toml,string,json}`

**Exit codes**

* `0` removed at least one element
* `1` key missing or value not present (no change)
* `2` type/path error or write error

---

### 8) `takopi config validate`

Validate that the current config is acceptable.

**Usage**

```sh
takopi config validate
takopi config validate --strict
```

**Behavior**

* Always parses TOML and runs config migrations (in-memory).
* Validates against Takopi’s settings schema.
* In `--strict` mode, also validates:

  * engine ids referenced by `default_engine` and `projects.*.default_engine` are available
  * project alias collisions with reserved chat commands
  * transport id validity (currently telegram only)
  * (optional) plugin allowlist consistency (if `plugins.enabled` is non-empty)

**Output**

* On success: `ok`
* On failure: human-readable error(s) and non-zero exit.

**Exit codes**

* `0` valid
* `2` invalid

---

## Validation policy for mutation commands

Mutation commands: `set`, `unset`, `add`, `remove`.

Validation is tricky because config can be temporarily incomplete (e.g., setting `bot_token` then `chat_id`).

Define `--validate` as:

* `auto` *(default)*:

  * If the **pre-edit config validates**, then the **post-edit config MUST validate** or the command fails **without writing**.
  * If the pre-edit config does **not** validate, the command writes changes but prints a warning:

    * “config is not yet valid; run `takopi config validate`”
* `always`:

  * Post-edit config MUST validate or the command fails without writing.
* `never`:

  * Skip validation entirely; only enforce “valid TOML” and structural path rules.

This gives safety for “normal operation” while still allowing bootstrapping.

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

## Security / redaction rules

Minimum required behavior:

* Never echo full secrets in confirmation output.

  * Example: after setting `transports.telegram.bot_token`, print:

    * `updated transports.telegram.bot_token = "********…"` (or `updated transports.telegram.bot_token`)

Default redaction should apply for:

* `list`
* `get` unless `--show-secrets` is passed

---

## Examples

Set defaults:

```sh
takopi config set default_engine claude
takopi config set watch_config true
takopi config unset default_project
```

Telegram setup:

```sh
takopi config set transports.telegram.bot_token "123456:ABCDEF..."
takopi config set transports.telegram.chat_id -1001234567890
takopi config set transports.telegram.topics.enabled true
takopi config set transports.telegram.files.enabled true
takopi config add transports.telegram.files.allowed_user_ids 123456789 --type toml
```

Projects:

```sh
takopi config set projects.happy-gadgets.path "~/dev/happy-gadgets"
takopi config set projects.happy-gadgets.default_engine claude
takopi config set projects.happy-gadgets.worktree_base master
```

Plugin allowlist:

```sh
takopi config add plugins.enabled takopi-engine-acme
takopi config add plugins.enabled takopi-transport-slack
```

Validate:

```sh
takopi config validate
takopi config validate --strict
```

---

## Implementation notes aligned with the current codebase

* Add `"config"` to `RESERVED_CLI_COMMANDS` so no engine plugin can register id `config`.
* Reuse `load_or_init_config()` and `write_config()` (but update/augment to support atomic write; current `write_config()` is not atomic).
* Reuse `migrate_config()`/`migrate_config_file()` logic, but prefer “migrate in-memory then write once” for config edits.
* For `list`, implement a deterministic flatten that walks dicts and emits dot-path keys (respect quoted segment output when needed).

---

If you want, I can also sketch the exact Typer shape (a `config_app = typer.Typer()` mounted under `create_app()`), plus the internal helpers you’ll want (`parse_keypath()`, `parse_value()`, `flatten_config()`, `set_path()`, `unset_path()`, `atomic_write_toml()`).
