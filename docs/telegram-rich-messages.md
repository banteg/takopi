# Telegram Rich Messages (Bot API 9.x+)

Takopi can send agent answers via [`sendRichMessage`](https://core.telegram.org/bots/api#sendrichmessage) when plain `sendMessage` + CommonMark would flatten GFM tables.

## Config

In `~/.takopi/takopi.toml`:

```toml
[transports.telegram]
# off | auto (default) | always
rich_messages = "auto"
```

- **`auto`**: use Rich Markdown when the final answer contains a GFM table (`| col |` + `|---|`) or long text with `##` headings.
- **`always`**: every final answer goes through `sendRichMessage` (Rich Markdown).
- **`off`**: legacy path only (`sendMessage` + sulguk entities).

Progress updates during a run still use `editMessageText` (regular messages).

## Upstream

This branch is intended for a PR to [banteg/takopi](https://github.com/banteg/takopi). Requires a recent Telegram client that renders rich messages.

## Install fork (local)

```powershell
uv tool install -U --with takopi-engine-cursor "takopi @ file:///C:/Users/savra/Desktop/code/takopi"
```

Or from your GitHub fork after push:

```powershell
uv tool install -U --with takopi-engine-cursor "takopi @ git+https://github.com/YOU/takopi.git@feat/telegram-rich-messages"
```
