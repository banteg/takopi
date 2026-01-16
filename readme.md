# takopi

🐙 *he just wants to help-pi*

telegram bridge for codex, claude code, opencode, pi. manage multiple projects and worktrees, stream progress, and resume sessions anywhere.

## features

- projects and worktrees: work on multiple repos/branches simultaneously, branches are git worktrees
- stateless resume: continue in chat or copy the resume line to pick up in terminal
- progress streaming: commands, tools, file changes, elapsed time
- parallel runs across agent sessions, per-agent-session queue
- works with telegram features like voice notes and scheduled messages
- file transfer: send files to the repo or fetch files/dirs back
- group chats and topics: map group topics to repo/branch contexts
- works with existing anthropic and openai subscriptions

## requirements

`uv` for installation (`curl -LsSf https://astral.sh/uv/install.sh | sh`)

python 3.14+ (`uv python install 3.14`)

at least one engine on PATH: `codex`, `claude`, `opencode`, or `pi`

## install

```sh
uv tool install -U takopi
```

## setup

run `takopi` and follow the setup wizard. it will help you:

1. create a bot token via @BotFather
2. pick a workflow (assistant, workspace, or handoff)
3. connect your chat
4. choose a default engine

workflows configure conversation mode, topics, and resume lines automatically:

- **assistant**: ongoing chat with auto-resume (recommended)
- **workspace**: forum topics bound to repos/branches
- **handoff**: reply-to-continue with terminal resume lines

## usage

```sh
cd ~/dev/happy-gadgets
takopi
```

send a message to your bot. prefix with `/codex`, `/claude`, `/opencode`, or `/pi` to pick an engine. reply to continue a thread.

register a project with `takopi init happy-gadgets`, then target it from anywhere with `/happy-gadgets hard reset the timeline`.

mention a branch to run an agent in a dedicated worktree `/happy-gadgets @feat/memory-box freeze artifacts forever`.

inspect or update settings with `takopi config list`, `takopi config get`, and `takopi config set`.

see [takopi.dev](https://takopi.dev/) for configuration, worktrees, topics, file transfer, and more.

## plugins

takopi supports entrypoint-based plugins for engines, transports, and commands.

see [`docs/how-to/write-a-plugin.md`](docs/how-to/write-a-plugin.md) and [`docs/reference/plugin-api.md`](docs/reference/plugin-api.md).

## development

see [`docs/reference/specification.md`](docs/reference/specification.md) and [`docs/developing.md`](docs/developing.md).

## community

[takopi dev](https://t.me/+jFvQTLE8m183MjBi) telegram group
