---
title: takopi
---

# Takopi documentation

Takopi runs coding agents on your computer and bridges them to Telegram. Send tasks from anywhere, watch progress stream live, pick up when back at the terminal. Scale from quick one-offs to multi-project workflows with topics and parallel worktrees.

<div class="hero-demo">
<div class="hero-chat">
<div class="chat-messages"></div>
</div>
<div class="hero-terminal">
<div class="terminal-content"></div>
</div>
</div>

## Quick start

```bash
uv tool install -U takopi
takopi --onboard
```

Onboarding walks you through bot setup and asks how you want to work. [Full install guide →](tutorials/install.md)

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

    [Get started →](tutorials/first-run.md)

</div>

You can change workflows later by editing `~/.takopi/takopi.toml`.

## Tutorials

Step-by-step guides for new users:

1. [Install & onboard](tutorials/install.md) — set up Takopi and your bot
2. [First run](tutorials/first-run.md) — send a task, watch it stream, continue the conversation
3. [Projects & branches](tutorials/projects-and-branches.md) — target repos from anywhere, run on feature branches
4. [Multi-engine](tutorials/multi-engine.md) — use different engines for different tasks

## How-to guides

- [Chat sessions](how-to/chat-sessions.md), [Topics](how-to/topics.md), [Projects](how-to/projects.md), [Worktrees](how-to/worktrees.md)
- [Voice notes](how-to/voice-notes.md), [File transfer](how-to/file-transfer.md), [Schedule tasks](how-to/schedule-tasks.md)
- [Write a plugin](how-to/write-a-plugin.md), [Add a runner](how-to/add-a-runner.md), [Dev setup](how-to/dev-setup.md)

## Reference

Exact options, defaults, and contracts:

- [Commands & directives](reference/commands-and-directives.md)
- [Configuration](reference/config.md)
- [Specification](reference/specification.md) — normative behavior
