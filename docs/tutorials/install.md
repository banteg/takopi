# Install and onboard

This tutorial walks you through installing Takopi, creating a Telegram bot, and generating your config file.

**What you'll have at the end:** A working `~/.takopi/takopi.toml` with your bot token, chat ID, workflow settings, and default engine.

## 1. Install Python 3.14 and uv

Install `uv`, the modern Python [package manager](https://docs.astral.sh/uv/):

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Install Python 3.14 with uv:

```sh
uv python install 3.14
```

## 2. Install Takopi

```sh
uv tool install -U takopi
```

Verify it's installed:

```sh
takopi --version
```

You should see something like `0.19.0`.

## 3. Install agent CLIs

Takopi shells out to agent CLIs. Install the ones you plan to use (or install them all now):

### Codex

```sh
npm install -g @openai/codex
```

Takopi uses the official Codex CLI, so your existing ChatGPT subscription applies. Run `codex` and sign in with your ChatGPT account.

### Claude Code

```sh
npm install -g @anthropic-ai/claude-code
```

Takopi uses the official Claude CLI, so your existing Claude subscription applies. Run `claude` and log in with your Claude account. Takopi defaults to subscription billing unless you opt into API billing in config.

### OpenCode

```sh
npm install -g opencode-ai@latest
```

OpenCode supports logging in with Anthropic for your Claude subscription or with OpenAI for your ChatGPT subscription, and it can connect to 75+ providers via Models.dev (including local models).

### Pi

```sh
npm install -g @mariozechner/pi-coding-agent
```

Pi can authenticate via a provider login or use API billing. You can log in with Anthropic (Claude subscription), OpenAI (ChatGPT subscription), GitHub Copilot, Google Cloud Code Assist (Gemini CLI), or Antigravity (Gemini 3, Claude, GPT-OSS), or choose API billing instead.

## 4. Run onboarding

Start Takopi without a config file. It will detect this and launch the setup wizard:

```sh
takopi
```

You'll see:

```
step 1: bot token

? do you already have a bot token from @BotFather? (yes/no)
```

If you don't have a bot token yet, answer **n** and Takopi will show you the steps.

## 5. Create a Telegram bot

If you answered **n**, follow these steps (or skip to step 6 if you already have a token):

1. Open Telegram and message [@BotFather](https://t.me/BotFather)
2. Send `/newbot` or use the mini app
3. Choose a display name (the obvious choice is "takopi")
4. Choose a username ending in `bot` (e.g., `my_takopi_bot`)

BotFather will congratulate you on your new bot and will reply with your token:

```
Done! Congratulations on your new bot. You will find it at
t.me/my_takopi_bot. You can now add a description, about
section and profile picture for your bot, see /help for a
list of commands.

Use this token to access the HTTP API:
123456789:ABCdefGHIjklMNOpqrsTUVwxyz

Keep your token secure and store it safely, it can be used
by anyone to control your bot.
```

Copy the token (the `123456789:ABC...` part).

!!! warning "Keep your token secret"
    Anyone with your bot token can control your bot. Don't commit it to git or share it publicly.

## 6. Enter your bot token

Paste your token when prompted:

```
? paste your bot token: ****
  validating...
  connected to @my_takopi_bot
```

Takopi validates the token by calling the Telegram API. If it fails, double-check you copied the full token.

## 7. Pick your workflow

Takopi shows three workflow previews:

=== "assistant"

    ongoing chat

    <div class="workflow-preview">
    <div class="msg msg-you">make happy wings fit</div><div class="clearfix"></div>
    <div class="msg msg-bot">done · codex · 8s · step 3</div><div class="clearfix"></div>
    <div class="msg msg-you">carry heavy creatures</div><div class="clearfix"></div>
    <div class="msg msg-bot">done · codex · 12s · step 5</div><div class="clearfix"></div>
    <div class="msg msg-you"><span class="cmd">/new</span></div><div class="clearfix"></div>
    <div class="msg msg-you">add flower pin</div><div class="clearfix"></div>
    <div class="msg msg-bot">done · codex · 6s · step 2</div><div class="clearfix"></div>
    </div>

=== "workspace"

    topics per branch

    <div class="workflow-preview">
    <div class="topic-bar"><span class="topic-active">happian @memory-box</span><span class="topic">takopi @master</span></div>
    <div class="msg msg-you">store artifacts forever</div><div class="clearfix"></div>
    <div class="msg msg-bot">done · codex · 10s · step 4</div><div class="clearfix"></div>
    <div class="msg msg-you">also freeze them</div><div class="clearfix"></div>
    <div class="msg msg-bot">done · codex · 6s · step 2</div><div class="clearfix"></div>
    </div>

=== "handoff"

    reply to continue

    <div class="workflow-preview">
    <div class="msg msg-you">make it go back in time</div><div class="clearfix"></div>
    <div class="msg msg-bot">done · codex · 8s · step 3<br><span class="resume">codex resume <span class="id-1">abc123</span></span></div><div class="clearfix"></div>
    <div class="msg msg-you">add reconciliation ribbon</div><div class="clearfix"></div>
    <div class="msg msg-bot">done · codex · 3s · step 1<br><span class="resume">codex resume <span class="id-2">def456</span></span></div><div class="clearfix"></div>
    <div class="msg msg-you"><div class="reply-quote">done · codex · 8s · step 3</div>more than once</div><div class="clearfix"></div>
    <div class="msg msg-bot">done · codex · 8s · step 5<br><span class="resume">codex resume <span class="id-1">abc123</span></span></div><div class="clearfix"></div>
    </div>

```
? how will you use takopi?
 ❯ assistant (ongoing chat, /new to reset)
   workspace (projects + branches, i'll set those up)
   handoff (reply to continue, terminal resume)
```

Each choice automatically configures conversation mode, topics, and resume lines:

| Workflow | Best for | What it does |
|----------|----------|--------------|
| **assistant** | Single developer, private chat | Chat mode (auto-resume), topics off, resume lines hidden. Use `/new` to start fresh. |
| **workspace** | Teams, multiple projects/branches | Chat mode, topics on, resume lines hidden. Each topic binds to a repo/branch. |
| **handoff** | Terminal-based workflow | Stateless (reply-to-continue), resume lines always shown. Copy resume line to terminal. |

!!! tip "Not sure which to pick?"
    Start with **assistant** (recommended). You can always change settings later in your config file.

## 8. Connect your chat

Depending on your workflow choice, Takopi shows different instructions:

**For assistant or handoff:**

```
step 3: connect chat

  1. open a chat with @my_takopi_bot
  2. send /start
  waiting for message...
```

**For workspace:**

```
step 3: connect chat

  set up a topics group:
  1. create a group and enable topics (settings → topics)
  2. add @my_takopi_bot as admin with "manage topics"
  3. send any message in the group
  waiting for message...
```

Once Takopi receives your message:

```
  got chat_id 123456789 for @yourusername (private chat)
```

!!! warning "Workspace requires a forum group"
    If you chose workspace and the chat isn't a forum-enabled supergroup with proper bot permissions, Takopi will warn you and offer to switch to assistant mode instead.

## 9. Choose your default engine

Takopi scans your PATH for installed agent CLIs:

```
step 4: default engine

takopi runs these engines on your computer. switch anytime with /agent.

  engine    status         install command
  ───────────────────────────────────────────
  codex     ✓ installed
  claude    ✓ installed
  opencode  ✗ not found    npm install -g opencode-ai@latest
  pi        ✗ not found    npm install -g @mariozechner/pi-coding-agent

? choose default engine:
 ❯ codex
   claude
```

Pick whichever you prefer. You can always switch engines per-message with `/codex`, `/claude`, etc.

## 10. Save your config

```
step 5: save config

? save config to ~/.takopi/takopi.toml? (yes/no)
```

Press **y** or **Enter** to save. You'll see:

```
✓ setup complete. starting takopi...
```

Takopi is now running and listening for messages!

## What just happened

Your config file lives at `~/.takopi/takopi.toml`. The exact contents depend on your workflow choice:

=== "assistant"

    === "takopi config"

        ```sh
        takopi config set default_engine "codex"
        takopi config set transport "telegram"
        takopi config set transports.telegram.bot_token "..."
        takopi config set transports.telegram.chat_id 123456789
        takopi config set transports.telegram.session_mode "chat"
        takopi config set transports.telegram.show_resume_line false
        takopi config set transports.telegram.topics.enabled false
        takopi config set transports.telegram.topics.scope "auto"
        ```

    === "toml"

        ```toml title="~/.takopi/takopi.toml"
        default_engine = "codex"
        transport = "telegram"

        [transports.telegram]
        bot_token = "..."
        chat_id = 123456789
        session_mode = "chat"       # auto-resume
        show_resume_line = false    # cleaner chat

        [transports.telegram.topics]
        enabled = false
        scope = "auto"
        ```

=== "workspace"

    === "takopi config"

        ```sh
        takopi config set default_engine "codex"
        takopi config set transport "telegram"
        takopi config set transports.telegram.bot_token "..."
        takopi config set transports.telegram.chat_id -1001234567890
        takopi config set transports.telegram.session_mode "chat"
        takopi config set transports.telegram.show_resume_line false
        takopi config set transports.telegram.topics.enabled true
        takopi config set transports.telegram.topics.scope "auto"
        ```

    === "toml"

        ```toml title="~/.takopi/takopi.toml"
        default_engine = "codex"
        transport = "telegram"

        [transports.telegram]
        bot_token = "..."
        chat_id = -1001234567890    # forum group
        session_mode = "chat"
        show_resume_line = false

        [transports.telegram.topics]
        enabled = true              # topics on
        scope = "auto"
        ```

=== "handoff"

    === "takopi config"

        ```sh
        takopi config set default_engine "codex"
        takopi config set transport "telegram"
        takopi config set transports.telegram.bot_token "..."
        takopi config set transports.telegram.chat_id 123456789
        takopi config set transports.telegram.session_mode "stateless"
        takopi config set transports.telegram.show_resume_line true
        takopi config set transports.telegram.topics.enabled false
        takopi config set transports.telegram.topics.scope "auto"
        ```

    === "toml"

        ```toml title="~/.takopi/takopi.toml"
        default_engine = "codex"
        transport = "telegram"

        [transports.telegram]
        bot_token = "..."
        chat_id = 123456789
        session_mode = "stateless"  # reply-to-continue
        show_resume_line = true     # always show resume lines

        [transports.telegram.topics]
        enabled = false
        scope = "auto"
        ```

This config file controls all of Takopi's behavior. You can edit it directly to change settings or add advanced features.

[Full config reference →](../reference/config.md)

## Re-running onboarding

If you ever need to reconfigure:

```sh
takopi --onboard
```

This will prompt you to update your existing config (it won't overwrite without asking).

## Troubleshooting

**"error: missing takopi config"**

Run `takopi` in a terminal with a TTY. The setup wizard only runs interactively.

**"failed to connect, check the token and try again"**

Make sure you copied the full token from BotFather, including the numbers before the colon.

**Bot doesn't respond to /start**

If you're still in onboarding, your terminal should show "waiting...". If you accidentally closed it, run `takopi` again and restart the setup.

**"error: already running"**

You can only run one Takopi instance per bot token. Find and stop the other process, or remove the stale lock file at `~/.takopi/takopi.lock`.

## Next

Learn more about conversation modes and how your workflow choice affects follow-ups.

[Conversation modes →](conversation-modes.md)
