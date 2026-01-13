# Multi-engine workflows

This tutorial shows you how to use different agents for different tasks and set up defaults so you don't have to think about it.

**What you'll learn:** Engine directives, persistent defaults, and when to use which agent.

## Why multiple engines?

Different agents have different strengths:

| Agent | Good at |
|-------|---------|
| **Codex** | Fast edits, shell commands, quick fixes |
| **Claude Code** | Complex refactors, architecture, long context |
| **OpenCode** | Open-source alternative, local models |
| **Pi** | Conversational, explanations |

You might want Codex for quick tasks and Claude for deep work—without manually specifying every time.

## 1. One-off engine selection

Prefix any message with `/<engine>`:

!!! user "You"
    /claude refactor this module to use dependency injection

!!! user "You"
    /codex add a --verbose flag to the CLI

!!! user "You"
    /pi explain how the event loop works in this codebase

The engine only applies to that message. The response will have a resume line for that engine:

!!! takopi "Takopi"
    done · claude · 8s<br>
    claude --resume abc123

When you reply, Takopi sees `claude --resume` and automatically uses Claude—you don't need to repeat `/claude`.

## 2. Engine + project + branch

Directives combine. Order doesn't matter:

!!! user "You"
    /claude /happy-gadgets @feat/di refactor to use dependency injection

Or:

!!! user "You"
    /happy-gadgets @feat/di /claude refactor to use dependency injection

Both do the same thing: run Claude in the `happy-gadgets` project on the `feat/di` branch.

!!! note "Directives are only parsed at the start"
    Everything after the first non-directive word is the prompt. `/claude fix /this/path` uses Claude with prompt "fix /this/path"—it doesn't try to parse `/this` as a directive.

## 3. Set a default engine for a chat

Use `/agent set` to change the default for the current scope:

!!! user "You"
    /agent set claude

Response:

!!! takopi "Takopi"
    chat default agent set to claude

Now all new conversations in this chat use Claude (unless you explicitly override with `/codex`).

Check the current default:

!!! user "You"
    /agent

Example response:

!!! takopi "Takopi"
    agent: claude (chat default)<br>
    defaults: topic: none, chat: claude, project: none, global: codex<br>
    available: codex, claude, opencode, pi

Clear it:

!!! user "You"
    /agent clear

Response:

!!! takopi "Takopi"
    chat default agent cleared.

## 4. Defaults in topics

If you use Telegram forum topics, `/agent set` applies per-topic:

!!! user "You"
    topic: Backend work<br>
    /agent set claude

!!! user "You"
    topic: Quick fixes<br>
    /agent set codex

Each topic remembers its own default.

## 5. Per-project defaults

Set a default engine in your project config:

```toml
[projects.happy-gadgets]
path = "~/dev/happy-gadgets"
default_engine = "claude"
```

Now `/happy-gadgets` tasks default to Claude, even if your global default is Codex.

## 6. Selection precedence

When Takopi picks an engine, it checks (highest to lowest):

1. **Resume line** — replying to `claude --resume ...` uses Claude
2. **Explicit directive** — `/codex ...` uses Codex
3. **Topic default** — `/agent set` in this forum topic
4. **Chat default** — `/agent set` in this chat
5. **Project default** — `default_engine` in project config
6. **Global default** — `default_engine` at the top of `takopi.toml`

This means: resume lines always win, then explicit directives, then the most specific default applies.

## 7. Practical patterns

**Pattern: Quick questions vs. deep work**

```
# Global default for quick stuff
default_engine = "codex"

# Project default for complex codebase
[projects.backend]
path = "~/dev/backend"
default_engine = "claude"
```

Simple messages go to Codex. `/backend` messages go to Claude.

**Pattern: Topic per engine**

Create forum topics like "Claude work" and "Codex tasks", then `/agent set` in each:

!!! user "You"
    topic: Claude deep-dives<br>
    /agent set claude

!!! user "You"
    topic: Quick Codex fixes<br>
    /agent set codex

Drag tasks to the right topic and the engine follows.

**Pattern: Override for specific tasks**

Even with defaults, you can always override:

!!! user "You"
    /codex just add a print statement here

Works regardless of what the default is.

## Recap

| Want to... | Do this |
|------------|---------|
| Use an engine once | `/claude ...` or `/codex ...` |
| Set default for chat | `/agent set claude` |
| Set default for topic | `/agent set ...` in the topic |
| Set default for project | `default_engine = "..."` in config |
| Set global default | `default_engine = "..."` at top of config |
| Check current default | `/agent` |
| Clear default | `/agent clear` |

## You're done!

That's the end of the tutorials. You now know how to:

- ✅ Install and configure Takopi
- ✅ Send tasks and continue conversations
- ✅ Cancel runs mid-flight
- ✅ Target repos and branches from chat
- ✅ Use multiple engines effectively

## Where to go next

**Want to do something specific?**

- [Enable forum topics](../how-to/topics.md) for organized threads
- [Transfer files](../how-to/file-transfer.md) between Telegram and your repo
- [Use voice notes](../how-to/voice-notes.md) to dictate tasks
- [Schedule tasks](../how-to/schedule-tasks.md) to run later

**Want to understand the internals?**

- [Architecture](../explanation/architecture.md) — how the pieces fit together
- [Routing and sessions](../explanation/routing-and-sessions.md) — how context resolution works
- [Specification](../reference/specification.md) — normative behavior contracts

**Need exact syntax?**

- [Commands & directives](../reference/commands-and-directives.md)
- [Configuration](../reference/config.md)
