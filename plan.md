# Takopi v0.9 Implementation Spec (projects + worktrees + ctx + incoming messages)

This document is implementation-ready. MUST/SHOULD/MAY are normative.

## Decisions (locked)
- Edit plan.md in place.
- Add requirements, acceptance criteria, edge cases, and test notes.
- Focus on v0.9 scope steps 1-5. Everything else is Appendix.
- If reply text includes a ctx line, ignore any new directives in the user message.
- If @branch does not exist, auto-create it from a default base branch.
- Branch names may include "/" and must not contain ".." or start with "/".

## Scope (v0.9)
1. Config: project aliases + `takopi init`.
2. Directive parsing: `/engine`, `/project`, `@branch`.
3. ctx footer in progress/final messages.
4. Worktree resolution and creation.
5. IncomingMessage abstraction; move Telegram parsing into transport.

## Non-goals (v0.9)
- OutgoingMessage abstraction / non-Telegram rendering.
- Plugin entry points and third-party transports.
- Command menu changes beyond existing engine commands.
- Multiple worktrees per branch or per thread.
- PR shortcuts like `@pr/123`.

## Glossary
- project alias: short name used as `/alias` in messages (e.g., `/z80`).
- directive line: the first non-empty line of a message, parsed for directives.
- ctx line: Takopi-owned footer line: `ctx: <project> @ <branch>` (branch optional).
- worktree: git worktree directory created under `<project_root>/<worktrees_dir>/<branch>`.

## 1) Config and `takopi init`

### 1.1 Config schema (TOML)
Top-level keys (existing + new):

```toml
default_engine = "codex"         # optional
default_project = "z80"          # optional
bot_token = "..."                # required
chat_id = 123                    # required

[projects.z80]
path = "~/dev/z80"               # required
worktrees_dir = ".worktrees"     # optional, default ".worktrees"
default_engine = "codex"         # optional, per-project override
worktree_base = "main"           # optional, base for new branches
```

### 1.2 Validation rules
- `projects` is optional. If absent, behavior stays as in v0.8 (run in startup cwd).
- Each project entry MUST include `path` (string, non-empty). `~` is expanded with `Path.expanduser`.
- `worktrees_dir` defaults to `.worktrees`. If relative, it is relative to `path`.
- `default_project` (if set) MUST match a configured project alias.
- Project aliases MUST NOT collide (case-insensitive) with engine ids or reserved commands (`cancel`).
- `default_engine` and per-project `default_engine` MUST match an available engine id.
- Config writing MAY reformat the file; preserving comments is out of scope.

### 1.3 `takopi init` behavior
Command: `takopi init [ALIAS]` (new Typer command).
- If ALIAS is provided, use it; otherwise prompt for alias.
- Defaults:
  - path: current working directory.
    - note: if `takopi init` is run inside a git worktree, this records the worktree path (not the main checkout). run it from the main checkout if you want the canonical repo root.
  - worktrees_dir: `.worktrees`
  - default_engine: resolved by `_default_engine_for_setup()`
  - worktree_base: resolved by the algorithm in section 4.3
- Writes/updates `~/.takopi/takopi.toml`:
  - Create file if missing.
  - Merge with existing keys; add or update `[projects.<alias>]`.
  - If `--default` flag is provided, set `default_project = "<alias>"`.
- If the alias already exists, ask for confirmation before overwriting that project entry.

Acceptance criteria
- Running `takopi init foo` adds `[projects.foo]` with path + worktrees_dir.
- Running `takopi init foo` from a worktree records the worktree path in `[projects.foo].path`.
- If `--default` is passed, `default_project` is set to `foo`.
- Invalid aliases (collision, empty, whitespace) error before writing.

Tests
- Unit tests for config validation: alias collisions, missing path, invalid default project.
- CLI test for init writing config (can use temp config path).

## 2) Directive parsing and context resolution

### 2.1 Directive grammar
Parse the first non-empty line. Tokenize by whitespace. Directives are a contiguous prefix of that line until the first non-directive token.

Directive tokens:
- `/name` or `/name@bot`:
  - `name` matches an engine id OR a project alias (case-insensitive).
  - If `name` matches neither, it is NOT a directive; parsing stops and the token is treated as prompt text.
- `@branch`:
  - `branch` is any non-empty, non-whitespace string.
  - `@` tokens are only treated as directives in the directive prefix.

Inline prompt:
- If the directive line contains non-directive text after the prefix, the remainder of the line (including that token and following text) is part of the prompt.
- If the directive line contains only directives, the prompt is the rest of the message after that line.

Constraints:
- At most one engine directive and one project directive are allowed; otherwise error.
- At most one `@branch` directive allowed; otherwise error.
- Project aliases cannot collide with engine ids (see 1.2), so `/name` is unambiguous.

### 2.2 Context resolution order (deterministic)
Given message text and optional reply-to text:

1) Parse resume token from reply-to text using `router.resolve_resume(text, reply_text)`.
2) Parse `ctx` line from reply-to text (section 3.2).
3) Parse directives from current message (section 2.1).

Then resolve:

- If a resume token is found:
  - Use that resume token and its engine.
  - If reply-to `ctx` is present, use it; otherwise keep existing behavior (in-memory mapping).
  - Ignore new directives in the current message.

- If no resume token but reply-to `ctx` is present:
  - Treat as a new message in that context (project/branch).
  - Ignore new directives in the current message.
  - Engine = project default if configured, else global default.

- If no resume token and no reply-to `ctx`:
  - Use directives (if any) to pick engine/project/branch.
  - If project not provided:
    - use `default_project` if set,
    - else no project (legacy behavior).

Prompt handling:
- For new threads, if the prompt is empty after directive stripping, pass the empty string to the runner (do not inject "continue").
- For resume threads, keep existing `_strip_resume_lines` behavior (default "continue" when all lines are resume lines).

Acceptance criteria
- Inline: `/codex /z80 @feat/name fix tests` yields engine=codex, project=z80, branch=feat/name, prompt="fix tests".
- Multiline: `/z80 @feat/name` + next line prompt yields same context.
- Reply with ctx line ignores new directives (engine/project/branch).

Tests
- Unit tests for parsing: inline vs multiline, unknown /command, duplicate directives, @branch with slashes.
- Unit tests for precedence: reply ctx wins over directives.

## 3) ctx footer

### 3.1 Output format
When a run has project context, every progress and final message MUST include a ctx line in the footer.

Canonical format:
- With branch: `ctx: <project> @ <branch>`
- Without branch: `ctx: <project>`

Footer order:
1) ctx line (if available)
2) resume line (if available), unchanged format from runner (`<engine> resume ...`)

If there is no project context (legacy mode), omit the ctx line entirely.

### 3.2 Parsing ctx from replies
- The parser scans reply text line-by-line for a line that starts with `ctx:` (case-insensitive).
- It accepts optional whitespace around tokens and optional `@ <branch>` suffix.
- If multiple ctx lines exist, use the last one.

Acceptance criteria
- Progress message includes ctx line as specified.
- Reply to a message containing ctx line applies that context even if the new message contains directives.

Tests
- Unit tests for ctx line parsing (case-insensitive, with/without branch).
- Rendering test to ensure footer order is ctx then resume.

## 4) Worktree resolution and creation

### 4.1 Path mapping
When `@branch` is present:
- `worktrees_root = project_root / worktrees_dir`
- `worktree_path = worktrees_root / branch`

Branch sanitization:
- Branch MUST be non-empty.
- Branch MUST NOT start with `/`.
- Branch MUST NOT contain a `..` path segment.
- Branch MAY contain `/` (nested dirs).
- After path resolution, `worktree_path` MUST be within `worktrees_root` (reject if it escapes).

If no `@branch`, `cwd = project_root`.

### 4.2 Worktree existence checks
- If `worktree_path` exists:
  - It MUST be a git worktree (verify `git -C <path> rev-parse --is-inside-work-tree`).
  - Otherwise, error.
- If `worktree_path` does not exist:
  - Create `worktrees_root` if missing.
  - Create worktree (see 4.3).

### 4.3 Base branch resolution for new branches
When branch does not exist locally:

Resolution order for base branch:
1) `projects.<alias>.worktree_base` if set.
2) `origin/HEAD` if present (via `git -C <root> symbolic-ref -q refs/remotes/origin/HEAD`).
3) current checked out branch at `project_root` (`git -C <root> branch --show-current`), if non-empty.
4) local `main` if it exists.
5) local `master` if it exists.
6) otherwise: error "cannot determine base branch".

Branch existence checks:
- If local branch exists: `git -C <root> show-ref --verify refs/heads/<branch>`.
- Else if remote branch exists: `git -C <root> show-ref --verify refs/remotes/origin/<branch>`.

Creation rules:
- If local branch exists: `git -C <root> worktree add <path> <branch>`.
- Else if remote branch exists: `git -C <root> worktree add -b <branch> <path> origin/<branch>`.
- Else: create from base: `git -C <root> worktree add -b <branch> <path> <base>`.

### 4.4 Run context and cwd usage
- Resolve `cwd` from project/worktree and bind it to a run-scoped contextvar.
- Use that contextvar as the default `base_dir` in `relativize_path` and `relativize_command`.
- Pass `cwd` to subprocess runners (JsonlSubprocessRunner) via `manage_subprocess(..., cwd=cwd)`.

Acceptance criteria
- `@feat/name` creates `<project_root>/.worktrees/feat/name` if missing.
- Invalid branch (`../x`, `/abs`) returns an error before any git commands.
- New runs execute with `cwd` set to resolved worktree path.

Tests
- Unit tests for branch sanitization and path containment.
- Integration test using a temp git repo to verify worktree creation and base branch selection.
- Test that `relativize_path` uses run context when base_dir is None.

## 5) IncomingMessage and Telegram parsing move

### 5.1 New IncomingMessage type
Add `takopi/api.py` (or `takopi/transport.py`) with:

```py
@dataclass(frozen=True)
class IncomingMessage:
    transport: str              # "telegram"
    chat_id: int
    message_id: int
    text: str
    reply_to_message_id: int | None
    reply_to_text: str | None
    sender_id: int | None
    raw: dict[str, Any] | None = None
```

### 5.2 Telegram transport adapter
- Move Telegram update parsing into `telegram.py` (new helper or class).
- Provide an async iterator that yields `IncomingMessage` instances.
- `bridge.poller` consumes `IncomingMessage` instead of raw Telegram dicts.
- Outgoing messages still use `BotClient` (no OutgoingMessage abstraction in v0.9).

Acceptance criteria
- `bridge` logic no longer reads Telegram dict fields directly.
- Telegram-specific parsing is confined to `telegram.py`.

Tests
- Unit test that Telegram update payload maps correctly into IncomingMessage.
- Existing bridge tests updated to use IncomingMessage.

## Acceptance checklist (v0.9)
- New message `/z80 @feat/name do x` runs in `<path>/.worktrees/feat/name`.
- Reply to a message containing `ctx: z80 @ feat/name` ignores any new directives.
- ctx line is present in progress and final messages.
- Missing branch auto-creates from resolved base.
- No changes required for existing users without `projects`.

## Appendix (out of scope for v0.9)
- OutgoingMessage abstraction and multi-transport rendering.
- Plugin entry points for runners/transports.
- Bot command menu strategy for projects.
- Multiple worktrees per branch or per thread.
- PR and branch shortcut syntax (`@+feat/name`, `@pr/123`).
