# takopi

🐙 *he just wants to help-pi*

telegram bridge for codex, claude code, opencode, pi, and [other agents](docs/adding-a-runner.md). runs the agent cli, streams progress, and supports resumable sessions.

## features

stateless resume, continue a thread in the chat or pick up in the terminal.

progress updates while agent runs (commands, tools, notes, file changes, elapsed time).

robust markdown rendering of output with a lot of quality of life tweaks.

parallel runs across threads, per thread queue support.

`/cancel` a running task.

**daemon mode**: manage multiple workspaces remotely without being tied to a specific repo.

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

## daemon mode

daemon mode runs takopi detached from any single repo, managing multiple workspaces remotely via telegram.

```sh
takopi daemon
```

### workspaces

workspaces are cloned repos that the daemon manages. each workspace has its own working tree where agents make changes.

**telegram commands:**

| command | description |
|---------|-------------|
| `/workspaces` | list all workspaces with status |
| `/workspace <name>` | switch to a workspace (subsequent prompts run there) |
| `/new` | start a new thread in current workspace |
| `/sessions` | list active sessions across all engines |
| `/drop <engine>` | drop session for an engine in current workspace |
| `/commit [message]` | commit changes in current workspace (auto-generates message if omitted) |

**cli commands:**

```sh
# add a workspace from git url or local path
takopi workspace add <repo-url> [--name myproject]

# list all workspaces
takopi workspace list

# show status (uncommitted changes, branch info)
takopi workspace status [name]

# link to local repo for easy syncing
takopi workspace link <name> <local-repo-path>

# git operations
takopi workspace pull <name>    # pull from origin
takopi workspace push <name>    # push to origin  
takopi workspace reset <name>   # reset to origin/HEAD
takopi workspace log <name>     # show recent commits
takopi workspace diff <name>    # show uncommitted changes

# create a pull request
takopi workspace pr <name> [--title "My PR"] [--draft]

# remove a workspace
takopi workspace remove <name> [--force]
```

### syncing changes to your local repo

after the agent makes changes in a workspace:

```sh
# in your local repo, add the workspace as a remote
git remote add takopi ~/.takopi/workspaces/<name>

# fetch and merge the changes
git fetch takopi
git merge takopi/main  # or the workspace branch

# push to origin
git push origin main
```

or use the link feature for a streamlined workflow:

```sh
# link workspace to local repo (one-time setup)
takopi workspace link myproject ~/dev/myproject

# then sync is automatic when you pull/push via the workspace commands
```

## notes

* the bot only responds to the configured `chat_id` (private or group)
* run only one takopi instance per bot token: multiple instances will race telegram's `getUpdates` offsets and cause missed updates

## development

see [`docs/specification.md`](docs/specification.md) and [`docs/developing.md`](docs/developing.md).
