I pulled the bundle and skimmed the v0.8.0 layout + recent changes. The “telegram transport abstraction” is real: `bridge.py` now depends on a `BotClient` protocol and you’ve isolated Telegram concerns into `takopi/telegram.py` (plus the new request outbox/rate-limit/backoff behavior). That’s a good first cut.

Below is what I think still needs shaping for a *public* API surface, and then a concrete UX proposal for the `/codex /z80 feat/name` workflow (phone-friendly, worktree isolation, minimal new concepts).

---

## 1) What’s still missing for a clean public API surface

### A. Right now the “API boundary” is still effectively the CLI

Even though Telegram HTTP is abstracted, most “composition points” are still internal-only:

* `cli.py` is Telegram-specific (`load_telegram_config()`, hard-wired `TelegramClient(token)`).
* `bridge.py` is still Telegram-shaped at the edges:

  * `poll_updates()` assumes Telegram update structure and yields raw Telegram `message` dicts.
  * IDs are Telegram types (`chat_id: int`, `message_id: int`).
  * rendering is Telegram-first (`prepare_telegram`, entities).
* config is named Telegram-specific (`load_telegram_config`) and top-level keys assume a single Telegram bridge.

If you want “public API” to mean **other people can embed Takopi** (or add transports cleanly), you’ll want a small, explicit “core” API module that:

* does not import Telegram,
* does not depend on env/config loading,
* composes via protocols/structs.

**Concrete shape suggestion**
Create a `takopi.api` (or `takopi.app`) layer with something like:

* `TakopiApp(...)` (core orchestrator)
* `Transport` protocol + `IncomingMessage` dataclass
* `RunnerRegistry/Router` (you already have `AutoRouter`)
* `run(app)` or `app.run_forever()`

Then `takopi/cli.py` becomes *one* consumer:

* load config
* instantiate `TelegramTransport`
* instantiate runners/router
* run the app

This makes “public API surface” real: users can import Takopi as a library and bring their own transport.

### B. Transport abstraction needs to cover *incoming message shape*, not just Telegram HTTP calls

Today you’ve abstracted “Telegram HTTP client calls” (`BotClient`), but not “what the bridge consumes”. The bridge still consumes Telegram message dicts.

To make transports pluggable, the bridge needs to consume a **Takopi-owned** message/event shape, e.g.:

```py
@dataclass(frozen=True)
class IncomingMessage:
    transport: str         # "telegram", "discord", "http", ...
    channel_id: str        # chat/channel/thread id (string to generalize)
    message_id: str
    text: str
    reply_to_message_id: str | None
    reply_to_text: str | None
    sender_id: str | None  # optional
```

Then Telegram transport is responsible for:

* polling `getUpdates`
* filtering by allowed chats
* mapping Telegram’s ints → strings (or keep ints but normalize)

And the bridge becomes transport-agnostic:

* “here is an IncomingMessage, produce outgoing messages via transport methods”.

This is the single biggest “API surface” unlock.

### C. Outgoing message payload should be transport-agnostic too

Today the core render path ends in Telegram text + entities. That’s fine for “officially Telegram”, but for a public API you want a neutral intermediate like:

```py
@dataclass(frozen=True)
class OutgoingMessage:
    text: str
    format: Literal["plain", "telegram_entities", "markdown"] = "plain"
    entities: list[dict] | None = None
```

Telegram transport can take `OutgoingMessage` and decide how to send.

You can still keep Telegram as the only built-in renderer; the point is: the app/bridge shouldn’t know what “entities” are.

### D. “Core types that are public” vs “internal implementation” needs an explicit contract

Right now, everything is importable but nothing is declared stable. For a public API surface, pick (and document) the stable set:

**Likely stable:**

* `takopi.model` (`ResumeToken`, events, `Action`)
* `takopi.runner` `Runner` protocol (and maybe `JsonlSubprocessRunner` helpers)
* `takopi.router.AutoRouter`
* `takopi.scheduler.ThreadScheduler`

**Likely internal / subject to change:**

* telegram implementation details (retry/backoff, edit coalescing)
* CLI config loading/writing paths
* render specifics

Then:

* export stable stuff from `takopi/__init__.py` (or `takopi.api`)
* prefix internal modules with `_` or clearly document they’re not stable

### E. Plugins: engines are “in-tree discoverable”, transports currently aren’t

Engines are discovered by iterating modules under `takopi.runners`. That’s convenient but not “public plugin” friendly.

If you actually want third parties to add transports/runners without forking, consider:

* Python entry points (`importlib.metadata.entry_points(group="takopi.runners")`)
* or a simple runtime registry:

  * `takopi.registry.register_runner_backend(EngineBackend)`
  * `takopi.registry.register_transport_backend(TransportBackend)`

You don’t have to do this immediately, but it’s part of “public API surface” if you mean “extensible ecosystem”.

### F. Multi-workdir support forces a “run context” concept somewhere in the public API

Your current execution assumes:

* Takopi runs in a single working directory (process `cwd`)
* All runners inherit that `cwd`

As soon as you introduce `/z80 @feat/name`, you have multiple possible `cwd` values **concurrently**. That implies an explicit “run context” must exist, either:

* passed to runners (`Runner.run(prompt, resume, *, cwd=...)`)
* or carried implicitly via a contextvar
* or resolved by building per-workdir runner instances (less ideal)

Even if you keep `Runner.run(prompt, resume)` unchanged, you’ll want a *core* concept like:

```py
@dataclass(frozen=True)
class RunContext:
    project: str | None
    cwd: Path
    branch: str | None
```

…and the bridge must attach it to each job.

This also interacts with rendering: `relativize_path()` and `relativize_command()` currently default to `Path.cwd()`, which becomes wrong once jobs run in different directories. You’ll either:

* thread `base_dir` everywhere, **or**
* make `relativize_*` consult a run-scoped contextvar when `base_dir=None`

(The contextvar approach is very low-diff and preserves existing call sites.)

---

## 2) UX proposal for “project alias + worktree branch”, phone-first

Goal: keep the interaction basically “one header line + prompt”, easy to type, and replies should “just work” without hidden state.

### A. Minimal message grammar (works as a single message, or multi-line)

**Proposed directive line format (first non-empty line):**

* optional engine: `/{engine}` (already exists)
* optional project: `/{project}` (new; from config)
* optional branch/worktree: `@{branch}` (new; explicit prefix avoids ambiguity)
* rest of message = prompt

Examples:

**New thread, explicit engine + project + branch**

```
/codex /z80 @feat/name
review this branch
```

**Default engine, just project + branch**

```
/z80 @feat/name
review this branch
```

**Default project + engine only**

```
/claude
explain the failing test
```

**Inline (single-line) variant**

```
/codex /z80 @feat/name review this branch
```

Why `@branch` instead of bare `feat/name`?

* avoids accidentally treating “fix” as a branch
* branch names with slashes still work
* it’s one extra character and is very phone-friendly

### B. “Context footer” in bot messages so replies are stateless

To make workflows efficient, **every progress/final message should include a Takopi-owned context line** *in addition to* the engine resume line.

Example footer:

```
ctx: z80 @ feat/name
`codex resume 019b...`
```

Key properties:

* easy to parse reliably (`ctx:` is Takopi-owned)
* doesn’t affect runner resume parsing (resume line still present and canonical)
* makes replies self-contained: reply-to-text carries both resume token and project/branch context

This is the big UX win: it avoids needing a local DB to map resume tokens → workdirs (you can still cache in-memory, but the chat log is the source of truth).

### C. `takopi init` UX

Your idea maps well:

**`takopi init` (run in a repo)**

* asks for alias (or takes CLI arg)
* writes to config:

  * project alias → absolute path
  * optional worktrees root (default `.worktrees`)
  * optional default engine override for that project

Example config extension:

```toml
default_engine = "codex"
bot_token = "..."
chat_id = 123

[projects.z80]
path = "~/dev/z80"
worktrees_dir = ".worktrees"
default_engine = "codex" # optional
```

You can keep existing engine tables unchanged (`[codex]`, `[claude]`, etc).

Also: you may want `takopi init --default` to set `default_project = "z80"` at top-level.

### D. Worktree semantics (simple, predictable)

Interpret `@branch` as “run in a worktree checked out at that branch”.

**Default worktree path**

* `<project_root>/.worktrees/<branch>`
  (matching your example: `~/dev/z80/.worktrees/feat/name`)

**Behavior**

* if the directory exists and is a git worktree: use it
* else: create it:

  * `git -C <root> worktree add <path> <branch>`
* if that fails because branch doesn’t exist:

  * return an actionable error message (“branch not found; create it or specify base”)

Phone-friendly optional sugar (if you want later):

* `@+feat/name` means “create branch off default base and create worktree”
* `@pr/123` could map to fetching PR refs if you want GitHub integration later

**Safety**

* sanitize branch → path:

  * forbid `..`, leading `/`, or anything that would escape `.worktrees`
  * allow `/` inside branch name if you want nested dirs (as in your example)

### E. Where this plugs into Takopi’s existing flow

Here’s the cleanest integration point without rewriting everything:

1. In the bridge loop, before deciding new thread vs resume:

   * parse directives from the user message (engine/project/branch)
   * also parse context from `reply_to_text` if present (from the bot footer)
2. Resolve resume token (existing behavior)
3. Determine effective context:

   * if resume token found: prefer context from `reply_to_text` footer; ignore new directives unless you explicitly want “move”
   * if new thread: use directives or default project
4. Set a **run-scoped base dir** for:

   * subprocess `cwd`
   * path relativization helpers
   * logging context fields (so logs show `project`, `branch`)

Because you already use structlog contextvars, a similar contextvar for `run_cwd` makes this nice and concurrency-safe.

### F. Bot command menu strategy (phone discoverability)

Telegram command menus get cluttered quickly if you add every project as a command, but for a small number of repos it’s great.

A good compromise:

* always register: `cancel`, engines (`codex`, `claude`, …)
* register **one** command: `projects` (lists aliases)
* optionally register project aliases *if count <= N* (say 10), otherwise don’t

So on a phone:

* you can type `/codex` from menu
* type space, then `/z80` (even if not in menu, it’s short)
* `@feat/name` is quick

### G. “Agent isolation” beyond worktrees (optional next step)

If “one worktree per branch” isn’t enough (multiple long-lived sessions on same branch), you can extend later to:

* `ctx: z80 @ feat/name #2`
* allocate worktree path `.worktrees/feat/name/<thread-shortid>`

…but I would start with “one per branch” because it matches your mental model and keeps the UX clean.

---

## 3) Practical gotchas to account for up front

### Codex/agent “trusted directory” prompts

If any runner refuses to operate in a newly created worktree until trusted:

* you’ll want the error surfaced cleanly (“codex requires trusting this directory; run `codex` once in …”)
* and/or a `takopi init` option that adds the project root (or worktrees root) to whatever trust mechanism the engine uses (if it’s safe and well-understood)

### Path relativization correctness with multi-project

As mentioned, today `relativize_*()` defaults to `Path.cwd()`. Once Takopi is running “globally” and jobs run in per-project workdirs, you’ll want:

* either explicit `base_dir` threading, or
* a run-scoped contextvar fallback

Without this, you’ll start seeing ugly absolute paths in progress output.

### Reply-while-running (your existing “enqueue once resume is ready” feature)

This feature is really nice and should keep working.
To make it work with project/worktree context:

* ensure the progress message includes the `ctx:` line from the beginning
* when a user replies before the resume token exists, the bridge should carry forward the context it already knows for that running task

(Using the `ctx:` line in the progress message is the simplest way; no extra state needed.)

---

## A concrete “v0.9” level scope that gets you both goals

If you want a tight plan that lands fast:

1. **Introduce `projects` in config + `takopi init`**

* purely config + CLI plumbing

2. **Add directive parsing (`/{project}` + `@branch`)**

* new-thread only at first

3. **Add `ctx:` footer line to progress/final messages**

* enables stateless reply behavior

4. **Add worktree creation**

* gated by `@branch`

5. **(API shaping) Introduce `IncomingMessage` + move Telegram message parsing into transport**

* bridge consumes Takopi-owned message objects
* this is the foundation for “public API transport plugins”

That sequence gives you immediate UX value without forcing a full transport refactor on day 1, but it sets you up to make the public API surface clean right after.

If you want, I can also point to the exact functions/modules I’d touch first in *this* codebase (parsing near `_strip_engine_command`, context line augmentation near `_send_or_edit_markdown`, and subprocess `cwd`/path relativization via a contextvar).
