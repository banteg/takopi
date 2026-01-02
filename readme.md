# takopi

🐙 *he just wants to help-pi*

telegram bridge for codex, claude code, opencode, pi, and [other agents](docs/adding-a-runner.md). runs the agent cli, streams progress, and supports resumable sessions.

## features

stateless resume, continue a thread in the chat or pick up in the terminal.

progress updates while agent runs (commands, tools, notes, file changes, elapsed time).

robust markdown rendering of output with a lot of quality of life tweaks.

parallel runs across threads, per thread queue support.

`/cancel` a running task.

## requirements

- `uv` for installation (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- python 3.14+ (uv can install it: `uv python install 3.14`)
- at least one engine installed:
  - `codex` on PATH (`npm install -g @openai/codex` or `brew install codex`)
  - `claude` on PATH (`npm install -g @anthropic-ai/claude-code`)
  - `opencode` on PATH (`npm install -g opencode-ai@latest`)
  - `pi` on PATH (`npm install -g @mariozechner/pi-coding-agent`)

## install

- `uv python install 3.14`
- `uv tool install -U takopi` to install as `takopi`
- or try it with `uvx takopi@latest`

## setup

1. get `bot_token` from [@BotFather](https://t.me/BotFather)
2. get `chat_id` from [@myidbot](https://t.me/myidbot)
3. send `/start` to the bot (telegram won't let it message you first)
4. run your agent cli once interactively in the repo to trust the directory

## config

global config `~/.takopi/takopi.toml`, repo-level config `.takopi/takopi.toml`

```toml
default_engine = "codex"

bot_token = "123456789:ABCdefGHIjklMNOpqrsTUVwxyz"
chat_id = 123456789

[codex]
# optional: profile from ~/.codex/config.toml
profile = "takopi"

[claude]
model = "sonnet"
allowed_tools = ["Bash", "Read", "Write", "WebSearch"]
dangerously_skip_permissions = false
# uses subscription by default, override to use api billing
use_api_billing = false

[pi]
model = "gpt-4.1"
provider = "openai"
```

## usage

start takopi in the repo you want to work on:

```sh
cd ~/dev/your-repo
takopi
# or override the default engine for new threads:
takopi claude
takopi opencode
takopi pi
```

resume lines always route to the matching engine; subcommands only override the default for new threads.

send a message to the bot.

start a new thread with a specific engine by prefixing your message with `/codex`, `/claude`, `/opencode`, or `/pi`.

to continue a thread, reply to a bot message containing a resume line.
you can also copy it to resume an interactive session in your terminal.

to stop a run, reply to the progress message with `/cancel`.

default: progress is silent, final answer is sent as a new message so you receive a notification, progress message is deleted.

if you prefer no notifications, `--no-final-notify` edits the progress message into the final answer.

## notes

* private chat only: the bot only responds to the configured `chat_id`
* run only one takopi instance per bot token: multiple instances will race telegram's `getUpdates` offsets and cause missed updates

## development

see [`docs/specification.md`](docs/specification.md) and [`docs/developing.md`](docs/developing.md).
