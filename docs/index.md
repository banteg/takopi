# Takopi documentation

Takopi runs coding agents on your computer and bridges them to Telegram. Send tasks from anywhere, watch progress stream in real time, pick up where you left off.

## Quick start

```bash
uv tool install -U takopi
takopi --onboard
```

Onboarding walks you through bot setup and asks how you want to work.

## Pick your workflow

<div class="grid cards" markdown>
-   :lucide-message-circle:{ .lg } **Assistant**

    ---

    Ongoing chat. New messages auto-continue; `/new` to reset.

    Best for: solo work, natural conversation flow.

    [Get started →](tutorials/first-run.md)

-   :lucide-folder-kanban:{ .lg } **Workspace**

    ---

    Forum topics bound to projects and branches.

    Best for: teams, organized multi-repo workflows.

    [Set up topics →](how-to/topics.md)

-   :lucide-terminal:{ .lg } **Handoff**

    ---

    Reply-to-continue. Copy resume lines to your terminal.

    Best for: explicit control, terminal-first workflow.

    [Learn more →](explanation/routing-and-sessions.md)

</div>

You can change workflows later by editing `~/.takopi/takopi.toml`.

## Tutorials

Step-by-step guides for new users:

1. [Install & onboard](tutorials/install-and-onboard.md) — set up Takopi and your bot
2. [First run](tutorials/first-run.md) — send a task, watch it stream, continue the conversation
3. [Projects & branches](tutorials/projects-and-branches.md) — target repos from anywhere, run on feature branches
4. [Multi-engine](tutorials/multi-engine.md) — use different agents for different tasks

## How-to guides

Goal-oriented recipes:

| Daily use | Extras | Extending |
|-----------|--------|-----------|
| [Chat sessions](how-to/chat-sessions.md) | [Voice notes](how-to/voice-notes.md) | [Write a plugin](how-to/write-a-plugin.md) |
| [Topics](how-to/topics.md) | [File transfer](how-to/file-transfer.md) | [Add a runner](how-to/add-a-runner.md) |
| [Projects](how-to/projects.md) | [Schedule tasks](how-to/schedule-tasks.md) | [Dev setup](how-to/dev-setup.md) |
| [Worktrees](how-to/worktrees.md) | | |

## Reference

Exact options, defaults, and contracts:

- [Commands & directives](reference/commands-and-directives.md)
- [Configuration](reference/config.md)
- [Specification](reference/specification.md) — normative behavior

## Core concepts

| Term | Meaning |
|------|---------|
| **Engine** | The CLI that does the work (`codex`, `claude`, `opencode`, `pi`) |
| **Project** | A named alias for a repo path |
| **Worktree** | A branch checkout in a separate directory (`@branch`) |
| **Resume line** | The `codex resume ...` footer that enables continuation |

## Troubleshooting

| Problem | Where to look |
|---------|---------------|
| Wrong repo/branch? | [Context resolution](reference/context-resolution.md) |
| Didn't continue? | [Commands & directives](reference/commands-and-directives.md) |
| Telegram weirdness? | [Telegram transport](reference/transports/telegram.md) |
| Why is it built this way? | [Architecture](explanation/architecture.md) |

## For plugin authors

- [Plugin API](reference/plugin-api.md) — stable `takopi.api` surface
- [Write a plugin](how-to/write-a-plugin.md)
- [Add a runner](how-to/add-a-runner.md)

## For LLM agents

- [Reference: For agents](reference/agents/index.md)
- [Repo map](reference/agents/repo-map.md)
- [Invariants](reference/agents/invariants.md)
