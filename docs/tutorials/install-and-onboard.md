# Install & onboard

You’ll install Takopi, connect it to Telegram, and generate a working `takopi.toml`.

## Prerequisites

- A Telegram account
- Python 3.14+ and `uv`
- At least one supported engine CLI on your `PATH` (`codex`, `claude`, `opencode`, or `pi`)

## 1) Install Takopi

```sh
uv tool install -U takopi
```

## 2) Run onboarding

Start Takopi:

```sh
takopi
```

If you want to re-run onboarding later:

```sh
takopi --onboard
```

The wizard walks you through:

1. Creating a bot token via [@BotFather](https://t.me/BotFather)
2. Capturing your `chat_id` (it listens for a message from you)
3. Choosing a default engine

Your configuration lives at `~/.takopi/takopi.toml`.

## 3) Verify minimal config

After onboarding you should have something like:

```toml
default_engine = "codex"
transport = "telegram"

[transports.telegram]
bot_token = "123456789:ABCdefGHIjklMNOpqrsTUVwxyz"
chat_id = 123456789
```

## Next

- [First run](first-run.md)
- [Config reference](../reference/config.md)

