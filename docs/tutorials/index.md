# Tutorials

Tutorials walk you through Takopi step-by-step. Follow them in order if you're new.

If you already know what you want ("enable topics", "use worktrees"), jump to **[How-to](../how-to/index.md)**.

## Prerequisites

Before starting, make sure you have:

- A **Telegram account**
- **Python 3.14+** and **uv** ([install uv](https://docs.astral.sh/uv/getting-started/installation/))
- At least one agent CLI on your `PATH`:

| Agent | Install |
|-------|---------|
| Codex | `npm install -g @openai/codex` |
| Claude Code | `npm install -g @anthropic-ai/claude-code` |
| OpenCode | `npm install -g opencode-ai@latest` |
| Pi | `npm install -g @mariozechner/pi-coding-agent` |

You only need one to get started. Takopi auto-detects what's available.

## The tutorials

### 1. Install and onboard

Set up Takopi, create a Telegram bot, and generate your config.

**Time:** ~5 minutes

[Start here →](install-and-onboard.md)

### 2. First run

Send your first task, watch it stream, and learn the core loop: run → continue → cancel.

**Time:** ~10 minutes

[Continue →](first-run.md)

### 3. Projects and branches

Register a repo as a project so you can target it from anywhere. Run tasks on feature branches without leaving your main worktree.

**Time:** ~10 minutes

[Continue →](projects-and-branches.md)

### 4. Multi-engine workflows

Use different agents for different tasks. Set defaults per chat or topic.

**Time:** ~5 minutes

[Continue →](multi-engine.md)

## What you'll build

By the end of these tutorials, you'll have:

```
~/.takopi/takopi.toml
├── bot_token + chat_id configured
├── default_engine set
└── projects.your-repo registered
```

And you'll know how to:

- Send tasks from Telegram and watch progress stream
- Continue conversations by replying
- Cancel runs mid-flight
- Target specific repos and branches
- Switch between agents on the fly

## After the tutorials

- **[How-to guides](../how-to/index.md)** — goal-oriented recipes (topics, file transfer, voice notes)
- **[Reference](../reference/index.md)** — exact config keys, commands, and contracts
- **[Explanation](../explanation/index.md)** — architecture and design rationale
