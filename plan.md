# Lockfile + Cleanup Plan

## Goals
- Prevent multiple takopi instances from sharing a bot token by introducing a per-config lock file.
- Prompt only when the lock looks stale, with a friendly “start anyway?” UX.
- Ensure clean shutdown on Ctrl+C and reliable lock cleanup.

## Lockfile behavior
- Location: alongside config file, same basename, `.lock` suffix.
  - `.takopi/takopi.toml` → `.takopi/takopi.lock`
  - `~/.takopi/takopi.toml` → `~/.takopi/takopi.lock`
- Format: JSON (already in `src/takopi/lockfile.py`) with `pid`, `hostname`, `started_at`, `config_path`, `token_fingerprint`, `argv`.

## Startup flow
1) Load config and validate `bot_token` + `chat_id`.
2) Compute `token_fingerprint`.
3) Acquire lock before polling Telegram.
4) If lock acquisition fails:
   - If state == `stale` and TTY:
     - Prompt: “Another instance could already be running. Start anyway? [y/N]”.
     - If `y`, delete the lock and retry once.
     - Otherwise exit non-zero.
   - If non-TTY or state != `stale`: exit non-zero with helpful message.

## Shutdown / cleanup
- Wrap main run in `try/finally` to release lock on normal exit or Ctrl+C.
- Catch `KeyboardInterrupt` in CLI to avoid stack traces, exit cleanly (e.g., 130).
- Keep `run_main_loop`’s existing `finally` to close the Telegram client.

## Messaging
- Friendly error context with lock metadata: pid, host, started_at, token fingerprint.
- Stale lock guidance:
  - TTY: prompt to delete and continue.
  - Non-TTY: explain manual deletion.

## Code touchpoints
- `src/takopi/cli.py`: add lock acquisition + cleanup around `_run_auto_router`.
- `src/takopi/lockfile.py`: add prompt helper + stale message tweaks.
- `readme.md`: document lockfile behavior and location.
- `tests/test_lockfile.py`: update expectations for new message wording if needed.

## Non-goals
- No `--force` or `--no-lock` flags.
- No prompts when a running instance is detected.
