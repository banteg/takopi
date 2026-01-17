# First run

This tutorial walks you through sending your first task, watching it execute, and learning the core interaction patterns.

**What you'll learn:** How Takopi streams progress, how to continue conversations, and how to cancel a run.

## 1. Start Takopi in a repo

Takopi runs agent CLIs in your current directory. Navigate to a repo you want to work in:

```sh
cd ~/dev/your-project
takopi
```

Takopi keeps running in your terminal. In Telegram, your bot will post a startup message like:

!!! takopi "Takopi"
    🐙 takopi is ready

    default: codex<br>
    engines: codex, claude<br>
    projects: none<br>
    mode: chat<br>
    topics: disabled<br>
    resume lines: hidden<br>
    working in: /Users/you/dev/your-project

The engines/projects list reflects your setup. This tells you:

- Which engine is the default
- Which engines are available (and any missing ones)
- Which projects are registered
- Which directory Takopi will run in

!!! note "Takopi runs where you start it"
    The agent will see files in your current directory. If you want to work on a different repo, stop Takopi (`Ctrl+C`) and restart it in that directory—or set up [projects](projects-and-branches.md) to switch repos from chat.

## 2. Send a task

Open Telegram and send a message to your bot:

!!! user "You"
    explain what this repo does


## 3. Watch progress stream

Takopi immediately posts a progress message and updates it as the agent works:

!!! takopi "Takopi"
    starting · codex · 0s

As the agent calls tools and makes progress, you'll see updates like:

!!! takopi "Takopi"
    working · codex · 12s · step 3

    ✓ tool: read: readme.md<br>
    ✓ tool: read: docs/index.md<br>
    ✓ tool: read: src/takopi/runner.py

The progress message is edited in-place.

## 4. See the final answer

When the agent finishes, Takopi sends a new message and replaces the progress message, so you get a notification.


!!! takopi "Takopi"
    done · codex · 11s · step 5
    
    Takopi is a Telegram bridge for AI coding agents (Codex, Claude Code, OpenCode, Pi). It lets you run agents from chat, manage multiple projects and git worktrees, stream progress (commands, file changes, elapsed time), and resume sessions from either chat or terminal. It also supports file transfer, group topics mapped to repo/branch contexts, and multiple engines via chat commands, with a plugin system for engines/transports/commands.

    codex resume 019bb89b-1b0b-7e90-96e4-c33181b49714


That last line is the **resume line**—it's how Takopi knows which conversation to continue.

## 5. Continue the conversation

How you continue depends on your mode.

**If you're in chat mode:** just send another message (no reply needed).

!!! user "You"
    now add tests for the API

Use `/new` any time you want a fresh thread.

**If you're in stateless mode:** **reply** to a message that has a resume line.

!!! takopi "Takopi"
    done · codex · 11s · step 5

    !!! user "You"
        what command line arguments does it support?

Takopi extracts the resume token from the message you replied to and continues the same agent session.

!!! tip "Reply-to-continue still works in chat mode"
    If resume lines are visible, replying to any older message branches the conversation from that point.
    Use `show_resume_line = true` if you want this behavior all the time.

!!! tip "Reset with /new"
    `/new` clears stored sessions for the current chat or topic.

## 6. Cancel a run

Sometimes you want to stop a run in progress—maybe you realize you asked the wrong question, or it's taking too long.

While the progress message is showing, tap the **cancel** button or reply to it with:

!!! takopi "Takopi"
    working · codex · 12s · step 3

    !!! user "You"
        /cancel

Takopi sends `SIGTERM` to the agent process and posts a cancelled status:

!!! failure ""
    cancelled · codex · 12s

    codex resume 019bb89b-1b0b-7e90-96e4-c33181b49714

If a resume token was already issued (and resume lines are enabled), it will still be included so you can continue from where it stopped.

!!! note "Cancel only works on progress messages"
    If the run already finished, there's nothing to cancel. Just send a new message or reply to continue.

## 7. Try a different engine

Want to use a different engine for one message? Prefix your message with `/<engine>`:

!!! user "You"
    /claude explain the error handling in this codebase

This uses Claude Code for just this message. The resume line will show `claude --resume ...`, and replies will automatically use Claude.

Available prefixes depend on what you have installed: `/codex`, `/claude`, `/opencode`, `/pi`.

!!! tip "Set a default engine"
    Use `/agent set claude` to make this chat (or topic) use Claude by default. Run `/agent` to see what's set.

## What just happened

Key points:

- Takopi spawns the agent CLI as a subprocess
- The agent streams JSONL events (tool calls, progress, answer)
- Takopi renders these as an editable progress message
- When done, the progress message is replaced with the final answer
- Chat mode auto-resumes; resume lines let you reply to branch

## Troubleshooting

**Progress message stuck on "starting" (or not updating)**

The agent might be doing something slow (large repo scan, network call). Wait a bit, or `/cancel` and try a more specific prompt.

**Agent CLI not found**

The agent CLI isn't on your PATH. Install the CLI for the engine you're using (e.g., `npm install -g @openai/codex`) and make sure the install location is in your PATH.

**Bot doesn't respond at all**

Check that Takopi is still running in your terminal. You should also see a startup message ("takopi is ready") from the bot in Telegram. If not, restart it.

**Resume doesn't work (starts a new conversation)**

Make sure you're **replying** to a message that contains a resume line. If you hid resume lines (`show_resume_line = false`), turn them on or use chat mode to continue by sending another message.

## Next

You've mastered the basics. Next, let's set up projects so you can target specific repos and branches from anywhere.

[Projects and branches →](projects-and-branches.md)
