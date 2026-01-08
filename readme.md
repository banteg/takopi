# takopi

🐙 *he just wants to help-pi*

telegram bridge for codex, claude code, opencode, pi, and [other agents](docs/adding-a-runner.md). manage multiple projects and worktrees, stream progress, and resume sessions anywhere.

## features

projects and worktrees: register repos with `takopi init`, target them via `/project`, route to branches with `@branch`.

stateless resume: continue a thread in the chat or pick up in the terminal.

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

run `takopi` and follow the interactive prompts. it will:

- help you create a bot token (via @BotFather)
- capture your `chat_id` from the most recent message you send to the bot
- check installed agents and set a default engine

to re-run onboarding (and overwrite config), use `takopi --onboard`.

run your agent cli once interactively in the repo to trust the directory.

## config

global config `~/.takopi/takopi.toml`

```toml
default_engine = "codex"

# optional, defaults to "telegram"
transport = "telegram"

[transports.telegram]
bot_token = "123456789:ABCdefGHIjklMNOpqrsTUVwxyz"
chat_id = 123456789

[codex]
# optional: profile from ~/.codex/config.toml
profile = "takopi"

[claude]
model = "sonnet"
# optional: defaults to ["Bash", "Read", "Edit", "Write"]
allowed_tools = ["Bash", "Read", "Edit", "Write", "WebSearch"]
dangerously_skip_permissions = false
# uses subscription by default, override to use api billing
use_api_billing = false

[opencode]
model = "claude-sonnet-4-20250514"

[pi]
model = "gpt-4.1"
provider = "openai"
# optional: additional CLI arguments
extra_args = ["--no-color"]
```

note: configs with top-level `bot_token` / `chat_id` are migrated to `[transports.telegram]` on startup.

## projects

register the current repo as a project alias:

```sh
takopi init z80
```

`takopi init` writes the repo root to `[projects.<alias>].path`. if you run it inside a git worktree, it resolves the main checkout and records that path instead of the worktree.

example:

```toml
default_project = "z80"

[projects.z80]
path = "~/dev/z80"
worktrees_dir = ".worktrees"
default_engine = "codex"
worktree_base = "master"
```

note: the default `worktrees_dir` lives inside the repo, so `.worktrees/` will
show up as untracked unless you ignore it (add to `.gitignore` or
`.git/info/exclude`), or set `worktrees_dir` to a path outside the repo.

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

list available transports (and override in a run):

```sh
takopi transports
takopi --transport telegram
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

* the bot only responds to the configured `chat_id` (private or group)
* run only one takopi instance per bot token: multiple instances will race telegram's `getUpdates` offsets and cause missed updates

## development

see [`docs/specification.md`](docs/specification.md) and [`docs/developing.md`](docs/developing.md).
