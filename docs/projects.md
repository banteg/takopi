# Projects and Worktrees

This doc covers project aliases, worktree behavior, and how Takopi resolves run
context from messages.

## Overview

Projects let you give a repo an alias (used as `/alias` in messages) and opt into
worktree-based runs via `@branch`.

- If no projects are configured, Takopi runs in the startup working directory.
- If a project is configured, `@branch` resolves/creates a git worktree and runs
  the task in that worktree.
- Progress/final messages include a `ctx:` footer when project context is active.

## Config schema

All config lives in `~/.takopi/takopi.toml`.

```toml
default_engine = "codex"       # optional
default_project = "z80"        # optional
transport = "telegram"         # optional, defaults to "telegram"

[transports.telegram]
bot_token = "..."              # required
chat_id = 123                  # required

[projects.z80]
path = "~/dev/z80"             # required (repo root)
worktrees_dir = ".worktrees"   # optional, default ".worktrees"
default_engine = "codex"       # optional, per-project override
worktree_base = "master"       # optional, base for new branches
```

Legacy config note: top-level `bot_token` / `chat_id` are auto-migrated into
`[transports.telegram]` on startup.

Note on `worktrees_dir`:

- The default `.worktrees` lives inside the repo root. You'll see it as an
  untracked directory (with nested git worktrees) unless you ignore it.
- Options:
  - add `.worktrees/` to your repo `.gitignore`, or
  - set `worktrees_dir` to a path outside the repo (e.g. `~/.takopi/worktrees/<alias>`).
  - add it to `.git/info/exclude` if you prefer a local-only ignore.

Validation rules:

- `projects` is optional.
- Each project entry must include `path` (string, non-empty).
- `default_project` must match a configured project alias.
- Project aliases cannot collide with engine ids or reserved commands (`/cancel`).
- `default_engine` and per-project `default_engine` must be valid engine ids.
- `transport` defaults to `"telegram"` when omitted; override per-run with `--transport`.

## `takopi init`

`takopi init <alias>` registers the current repo as a project alias.

Important behavior:

- The stored `path` is the **main checkout** of the repo, even if you run
  `takopi init` inside a worktree. Takopi resolves the repo root via the git
  common dir and writes that path to `[projects.<alias>].path`.
- `worktree_base` is set from the current repo using this resolution order:
  `origin/HEAD` → current branch → `master` → `main`.

## Directives and context resolution

Takopi parses the first non-empty line of a message for a directive prefix.

Supported directives:

- `/engine` or `/engine@bot`: chooses the engine
- `/project`: chooses a project alias
- `@branch`: chooses a git branch/worktree

Rules:

- Directives must be a contiguous prefix of the line; parsing stops at the first
  non-directive token.
- At most one engine directive, one project directive, and one `@branch` are
  allowed (duplicates are errors).
- If a reply contains a `ctx:` line, Takopi **ignores new directives** and uses
  the reply context.

## Context footer (`ctx:`)

When a run has project context, Takopi appends a footer line rendered as inline
code (backticked):

- With branch: `` `ctx: <project> @ <branch>` ``
- Without branch: `` `ctx: <project>` ``

The `ctx:` line is parsed from replies and takes precedence over new directives.

## Worktree resolution

When `@branch` is present:

```
worktrees_root = <project.path> / <worktrees_dir>
worktree_path = worktrees_root / <branch>
```

Branch validation:

- Must be non-empty
- Must not start with `/`
- Must not contain `..` path segments
- May include `/` (nested directories)
- The resolved worktree path must stay within `worktrees_root`

Worktree creation rules:

1) If `worktree_path` exists:
   - It must be a git worktree or Takopi errors.
2) If it does not exist:
   - If local branch exists: `git worktree add <path> <branch>`
   - Else if remote `origin/<branch>` exists:
     `git worktree add -b <branch> <path> origin/<branch>`
   - Else:
     `git worktree add -b <branch> <path> <base>`

Base branch selection:

1) `projects.<alias>.worktree_base` (if set)
2) `origin/HEAD` (if present)
3) current checked out branch
4) `master` if it exists
5) `main` if it exists
6) otherwise error

When `@branch` is omitted:

- Takopi runs in `<project.path>` (the main checkout).

## Examples

Start a new thread in a worktree:

```
/z80 @feat/streaming fix flaky test
```

Reply to a progress message to continue in the same context:

```
ctx: z80 @ feat/streaming
```
