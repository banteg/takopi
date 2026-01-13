# Takopi documentation

> Telegram bridge for coding agents (Codex, Claude Code, OpenCode, Pi).

Takopi lets you run an engine CLI in a local repo while controlling it from Telegram: send a task, stream updates, and continue safely (reply-to-continue, topics, or sessions).

## Choose your path

<div class="grid cards" markdown>
-   :lucide-sparkles:{ .lg } **I’m new / I want to get it running**
    
    ---

    Start with [Tutorials](tutorials/index.md).

    - [Install & onboard](tutorials/install-and-onboard.md)
    - [First run](tutorials/first-run.md)

-   :lucide-compass:{ .lg } **I know what I want to do**

    ---

    Use [How-to guides](how-to/index.md).

    - [Projects](how-to/projects.md) and [Worktrees](how-to/worktrees.md)
    - [Topics](how-to/topics.md) and [Route by chat](how-to/route-by-chat.md)
    - [File transfer](how-to/file-transfer.md) and [Voice notes](how-to/voice-notes.md)

-   :lucide-book:{ .lg } **I need exact knobs, defaults, and contracts**

    ---

    Go straight to [Reference](reference/index.md).

    - [Commands & directives](reference/commands-and-directives.md)
    - [Configuration](reference/config.md)
    - [Specification](reference/specification.md) (normative behavior)

-   :lucide-lightbulb:{ .lg } **I'm trying to understand the design**

    ---

    Read [Explanation](explanation/index.md).

    - [Architecture](explanation/architecture.md)
    - [Routing & sessions](explanation/routing-and-sessions.md)
    - [Plugin system](explanation/plugin-system.md)

</div>

## Quick start

If you just want to see it work end-to-end:

```bash
# Install
uv tool install -U takopi

# Configure Telegram + defaults
takopi --onboard

# Run in a repo
cd /path/to/your/repo
takopi
```

Then open Telegram and send a task to your bot.

## Core concepts

* **Engine**: the CLI that actually does the work (e.g. `codex`, `claude`, `opencode`, `pi`).
* **Project**: a named alias for a repo path (so you can run from anywhere).
* **Worktree / branch selection**: pick where work should happen (`@branch`).
* **Continuation**: how Takopi safely “continues” a run:

  * reply-to-continue (always available)
  * forum topics (thread-bound continuation)
  * chat sessions (auto-resume)
* **Contract**: the stable rules (resume lines, event ordering, rendering expectations) in the
  [Specification](reference/specification.md) and runner contract tests.

## For plugin authors

Start here:

* [Plugin API](reference/plugin-api.md) — **stable** `takopi.api` surface for plugins
* [Write a plugin](how-to/write-a-plugin.md)
* [Add a runner](how-to/add-a-runner.md)

If you’re contributing to core:

* [Dev setup](how-to/dev-setup.md)
* [Module map](explanation/module-map.md)

## For LLM agents

In the docs, start here:

* [Reference: For agents](reference/agents/index.md)
* [Repo map](reference/agents/repo-map.md)
* [Invariants](reference/agents/invariants.md)

## Where to look when something feels “off”

* “Why didn’t it route to the right repo/branch?” → [Context resolution](reference/context-resolution.md)
* “Why didn’t it continue where I left off?” → [Commands & directives](reference/commands-and-directives.md) and [Specification](reference/specification.md)
* “Why did Telegram messages behave weirdly?” → [Telegram transport](reference/transports/telegram.md)
* “Why is it built this way?” → [Architecture](explanation/architecture.md)

## Legacy portals

These pages remain as curated pointers to preserve old links:

- [User guide](user-guide.md)
- [Plugins](plugins.md)
- [Developing](developing.md)
