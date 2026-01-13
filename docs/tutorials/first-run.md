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
    agents: codex, claude<br>
    projects: none<br>
    working in: /Users/you/dev/your-project

The engines/projects list reflects your setup. This tells you:

- Which engine is the default
- Which agents are available (and any missing ones)
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

To follow up, **reply** to the bot's message:

!!! takopi "Takopi"
    done · codex · 11s · step 5

    !!! user "You"
        what command line arguments does it support?

Takopi extracts the resume token from the message you replied to and continues the same agent session. The agent remembers everything from before.

!!! takopi "Takopi"
    done · codex · 47s · step 11

    CLI Args

    - Global/auto-router (when you run just takopi): --version, --final-notify/--no-final-notify, --onboard/--no-onboard, --transport <id>, --debug/--no-debug. This is the same option set used by engine subcommands.
    - init [alias]: optional positional alias, plus --default to set the project as default_project.
    - chat-id: --token <bot_token>, --project <alias> to store a captured chat id into the project config.
    - plugins: --load/--no-load to validate plugin imports.
    - Engine subcommands: one per engine id; built-ins are codex, claude, opencode, pi, plus any plugin engines. Each accepts --final-notify, --onboard, --transport, --debug.

    If you want, I can also summarize takopi --help output verbatim for your local build.

    codex resume 019bb89b-1b0b-7e90-96e4-c33181b49714

!!! tip "You can reply to any message with a resume line"
    The resume line doesn't have to be in the most recent message. Reply to any earlier message to "branch" the conversation from that point.

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

Want to use a different agent for one message? Prefix your message with `/<engine>`:

!!! user "You"
    /claude explain the error handling in this codebase

This uses Claude Code for just this message. The resume line will show `claude --resume ...`, and replies will automatically use Claude.

Available prefixes depend on what you have installed: `/codex`, `/claude`, `/opencode`, `/pi`.

## What just happened

Key points:

- Takopi spawns the agent CLI as a subprocess
- The agent streams JSONL events (tool calls, progress, answer)
- Takopi renders these as an editable progress message
- When done, the progress message is replaced with the final answer
- The resume line lets you continue the conversation

## The core loop

You now know the three fundamental interactions:

| Action | How |
|--------|-----|
| **Start** | Send a message to your bot |
| **Continue** | Reply to any message with a resume line |
| **Cancel** | Tap **cancel** on a progress message |

Everything else in Takopi builds on this loop.

## Troubleshooting

**Progress message stuck on "starting" (or not updating)**

The agent might be doing something slow (large repo scan, network call). Wait a bit, or `/cancel` and try a more specific prompt.

**Agent CLI not found**

The agent CLI isn't on your PATH. Install the CLI for the engine you're using (e.g., `npm install -g @openai/codex`) and make sure the install location is in your PATH.

**Bot doesn't respond at all**

Check that Takopi is still running in your terminal. You should also see a startup message ("takopi is ready") from the bot in Telegram. If not, restart it.

**Resume doesn't work (starts a new conversation)**

Make sure you're **replying** to a message, not sending a new one. The reply must be to a message that contains a resume line.

## Next

You've mastered the basics. Next, let's set up projects so you can target specific repos and branches from anywhere.

[Projects and branches →](projects-and-branches.md)
