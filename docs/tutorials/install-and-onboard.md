# Install and onboard

This tutorial walks you through installing Takopi, creating a Telegram bot, and generating your config file.

**What you'll have at the end:** A working `~/.takopi/takopi.toml` with your bot token, chat ID, and default engine.

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

You should see something like `0.18.0`.

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

You'll see something like:

```
welcome to takopi!

let's set up your telegram bot.

step 1: telegram bot setup

? do you have a telegram bot token? (yes/no)
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

## 7. Capture your chat ID

Takopi needs to know which chat to send messages to. It will listen for a message from you:

```
  send /start to @my_takopi_bot (works in groups too)
  waiting...
```

Open Telegram and send `/start` (or any message) to your bot. Takopi will capture the chat:

```
  got chat_id 123456789 from @yourusername
  sent confirmation message
```

!!! tip "Using Takopi in a group"
    You can also send a message in a group where the bot is a member. Takopi will capture that group's chat ID instead. This is useful if you want multiple people to share the same bot.

## 8. Choose your default engine

Takopi scans your PATH for installed agent CLIs:

```
step 2: agent cli tools

  agent     status         install command
  ───────────────────────────────────────────
  codex     ✓ installed
  claude    ✓ installed
  opencode  ✗ not found    npm install -g opencode-ai@latest
  pi        ✗ not found    npm install -g @mariozechner/pi-coding-agent

? choose default agent:
 ❯ codex
   claude
```

Pick whichever you prefer. You can always switch engines per-message later.

## 9. Save your config

Takopi shows you a preview of what it will save:

```
step 3: save configuration

  ~/.takopi/takopi.toml

  default_engine = "codex"
  transport = "telegram"

  [transports.telegram]
  bot_token = "123456789:ABC..."
  chat_id = 123456789

? save this config to ~/.takopi/takopi.toml? (yes/no)
```

Press **Enter** to save. You'll see:

```
  config saved to ~/.takopi/takopi.toml

setup complete. starting takopi...
```

Takopi is now running and listening for messages!

## What just happened

Your config file lives at `~/.takopi/takopi.toml`:

```toml title="~/.takopi/takopi.toml"
default_engine = "codex" # new threads use this
transport = "telegram"   # how Takopi talks to you

[transports.telegram]
bot_token = "..."        # your bot's API key
chat_id = 123456789      # where to send messages
```

This config file controls all of Takopi's behavior. You'll edit it directly for advanced features.

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

Now that Takopi is configured, let's send your first task.

[First run →](first-run.md)
