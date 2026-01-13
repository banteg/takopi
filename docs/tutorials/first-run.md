# First run

This tutorial walks you through sending your first task, watching it execute, and learning the core interaction patterns.

**What you'll learn:** How Takopi streams progress, how to continue conversations, and how to cancel a run.

## 1. Start Takopi in a repo

Takopi runs agent CLIs in your current directory. Navigate to a repo you want to work in:

```sh
cd ~/dev/your-project
takopi
```

You should see:

```
takopi v0.17.1 • codex • telegram
listening...
```

This tells you:
- Which version is running
- Which engine is the default (`codex`)
- Which transport is active (`telegram`)

!!! note "Takopi runs where you start it"
    The agent will see files in your current directory. If you want to work on a different repo, stop Takopi (`Ctrl+C`) and restart it in that directory—or set up [projects](projects-and-branches.md) to switch repos from chat.

## 2. Send a task

Open Telegram and send a message to your bot:

```
explain what this repo does
```

## 3. Watch progress stream

Takopi immediately posts a progress message and updates it as the agent works:

```
⏳ thinking...
```

As the agent calls tools and makes progress, you'll see updates:

```
⏳ working...
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📂 Read README.md
📂 Read src/main.py
📂 Read pyproject.toml
```

The progress message is edited in-place (rate-limited to avoid Telegram API limits).

## 4. See the final answer

When the agent finishes, Takopi:
1. Deletes the progress message
2. Posts the final answer as a new message

```
This is a Python CLI tool that converts Markdown files to
HTML. It uses the `mistune` library for parsing and
supports syntax highlighting via `pygments`.

The main entry point is `src/main.py`, which accepts a
file path and outputs HTML to stdout.

──────────────────────────────────
codex --resume abc123def456
```

That last line is the **resume line**—it's how Takopi knows which conversation to continue.

## 5. Continue the conversation

To follow up, **reply** to the bot's message:

```
↩️ (replying to the bot's answer)

what command line arguments does it support?
```

Takopi extracts the resume token from the message you replied to and continues the same agent session. The agent remembers everything from before.

```
The CLI supports these arguments:

  --output, -o    Output file path (default: stdout)
  --style, -s     Pygments style for syntax highlighting
  --no-highlight  Disable syntax highlighting

Example:
  python -m myproject input.md -o output.html --style monokai

──────────────────────────────────
codex --resume abc123def456
```

!!! tip "You can reply to any message with a resume line"
    The resume line doesn't have to be in the most recent message. Reply to any earlier message to "branch" the conversation from that point.

## 6. Cancel a run

Sometimes you want to stop a run in progress—maybe you realize you asked the wrong question, or it's taking too long.

While the progress message is showing, reply to it with:

```
/cancel
```

Takopi sends `SIGTERM` to the agent process and posts a cancelled status:

```
⚠️ cancelled

──────────────────────────────────
codex --resume abc123def456
```

The resume line is still included, so you can continue from where it stopped.

!!! note "Cancel only works on progress messages"
    If the run already finished, there's nothing to cancel. Just send a new message or reply to continue.

## 7. Try a different engine

Want to use a different agent for one message? Prefix your message with `/<engine>`:

```
/claude explain the error handling in this codebase
```

This uses Claude Code for just this message. The resume line will show `claude --resume ...`, and replies will automatically use Claude.

Available prefixes depend on what you have installed: `/codex`, `/claude`, `/opencode`, `/pi`.

## What just happened

Here's the full message lifecycle:

```
┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│   You send   │───▶│   Takopi     │───▶│  Agent CLI   │
│   message    │    │   bridges    │    │  (codex)     │
└──────────────┘    └──────────────┘    └──────────────┘
                           │                    │
                           │◀───────────────────┤
                           │   JSONL events     │
                           │   (streaming)      │
                           ▼                    │
                    ┌──────────────┐            │
                    │  Progress    │            │
                    │  message     │            │
                    │  (edited)    │            │
                    └──────────────┘            │
                           │                    │
                           │◀───────────────────┘
                           │   completed event
                           ▼
                    ┌──────────────┐
                    │  Final       │
                    │  answer      │
                    │  + resume    │
                    └──────────────┘
```

Key points:
- Takopi spawns the agent CLI as a subprocess
- The agent streams JSONL events (tool calls, progress, answer)
- Takopi renders these as an editable progress message
- When done, the progress is replaced with the final answer
- The resume line lets you continue the conversation

## The core loop

You now know the three fundamental interactions:

| Action | How |
|--------|-----|
| **Start** | Send a message to your bot |
| **Continue** | Reply to any message with a resume line |
| **Cancel** | Reply `/cancel` to a progress message |

Everything else in Takopi builds on this loop.

## Troubleshooting

**Progress message stuck on "thinking..."**

The agent might be doing something slow (large repo scan, network call). Wait a bit, or `/cancel` and try a more specific prompt.

**"error: codex not found"**

The agent CLI isn't on your PATH. Install it (`npm install -g @openai/codex`) and make sure the install location is in your PATH.

**Bot doesn't respond at all**

Check that Takopi is running in your terminal. If you see `listening...`, it's working. If not, restart it.

**Resume doesn't work (starts a new conversation)**

Make sure you're **replying** to a message, not sending a new one. The reply must be to a message that contains a resume line.

## Next

You've mastered the basics. Next, let's set up projects so you can target specific repos and branches from anywhere.

[Projects and branches →](projects-and-branches.md)
