# Telegram Codex Bridge (Codex)

Route Telegram replies back into Codex sessions. Includes three options:

1. Non-interactive `codex exec` + `codex exec resume`.
2. `codex mcp-server` with MCP stdio JSON-RPC.
3. tmux injection for interactive Codex sessions.

All options store a mapping from `(chat_id, bot_message_id)` to a route so replies can be routed correctly.

## Install

1. Ensure `uv` is installed.
2. Use the scripts in this folder as-is (no extra dependencies).
3. Set `TELEGRAM_BOT_TOKEN` and (optionally) `ALLOWED_CHAT_IDS`.

## Option 1: exec/resume

Run:

```bash
export TELEGRAM_BOT_TOKEN="123:abc"
export BRIDGE_DB="./bridge_routes.sqlite3"
export CODEX_CMD="codex"
export CODEX_WORKSPACE="/path/to/repo"
export CODEX_EXEC_ARGS="--full-auto"
export STARTUP_CHAT_IDS="123456789"  # optional; defaults to ALLOWED_CHAT_IDS if set
export STARTUP_MESSAGE="✅ exec_bridge started (codex exec)."  # optional; PWD is appended
uv run exec_bridge.py
```

## Option 2: MCP server

Run:

```bash
export TELEGRAM_BOT_TOKEN="123:abc"
export BRIDGE_DB="./bridge_routes.sqlite3"
export CODEX_MCP_CMD="codex mcp-server"
export CODEX_WORKSPACE="/path/to/repo"
export CODEX_SANDBOX="workspace-write"
export CODEX_APPROVAL_POLICY="never"
uv run mcp_bridge.py
```

## Option 3: tmux

Reply injector:

```bash
export TELEGRAM_BOT_TOKEN="123:abc"
export BRIDGE_DB="./bridge_routes.sqlite3"
export ALLOWED_CHAT_IDS="123456789"
uv run tmux_reply_bot.py
```

Notifier (call from your existing hook):

```bash
uv run tmux_notify.py --chat-id "$CHAT_ID" --tmux-target "codex1:0.0" --text "$TURN_TEXT"
```

## Files

- `bridge_common.py`: shared Telegram client, chunking, and routing store
- `exec_bridge.py`: codex exec + resume bridge
- `mcp_bridge.py`: MCP stdio JSON-RPC bridge
- `tmux_notify.py`: tmux notifier helper
- `tmux_reply_bot.py`: tmux reply injector
