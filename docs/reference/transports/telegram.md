# Telegram Transport

## Overview

`TelegramClient` is the single transport for Telegram writes. It owns a
`TelegramOutbox` that serializes send/edit/delete operations, applies
coalescing, and enforces rate limits + retry-after backoff.

This document captures current behavior so transport changes stay intentional.

## Flow

1. Engine CLI emits JSONL events.
2. We render progress on every step and diff against the last output.
3. Only deltas enqueue a Telegram edit.
4. High-value messages enqueue a send.
5. All writes go through the outbox.

## Incoming messages

`parse_incoming_update` accepts text messages and voice notes.

### Voice transcription

If voice transcription is enabled, takopi downloads the voice payload from Telegram,
transcribes it with OpenAI, and routes the transcript through the same command and
directive pipeline as typed text.

Configuration (under `[transports.telegram]`):

=== "takopi config"

    ```sh
    takopi config set transports.telegram.voice_transcription true
    takopi config set transports.telegram.voice_transcription_model "gpt-4o-mini-transcribe"

    # local OpenAI-compatible transcription server (optional)
    takopi config set transports.telegram.voice_transcription_base_url "http://localhost:8000/v1"
    takopi config set transports.telegram.voice_transcription_api_key "local"
    ```

=== "toml"

    ```toml
    voice_transcription = true
    voice_transcription_model = "gpt-4o-mini-transcribe" # optional
    voice_transcription_base_url = "http://localhost:8000/v1" # optional
    voice_transcription_api_key = "local" # optional
    ```

Set `OPENAI_API_KEY` in the environment (or `voice_transcription_api_key` in config).
If transcription is enabled but no API key is available or the audio download fails,
takopi replies with a short error and skips the run.

To use a local OpenAI-compatible Whisper server, set `voice_transcription_base_url`
(and `voice_transcription_api_key` if the server expects one). This keeps engine
requests on their own base URL without relying on `OPENAI_BASE_URL`. If your server
requires a specific model name, set `voice_transcription_model` (for example,
`whisper-1`).

### Trigger mode (mentions-only)

Telegram’s bot privacy mode stops bots from seeing every message by default, but
**admins always receive all messages** in groups. If you promote takopi to admin,
Telegram will deliver every update even when privacy mode is enabled.

To restore “only respond when invoked” behavior, use trigger mode:

- `all` (default): any message can start a run (subject to ignore rules).
- `mentions`: only start when explicitly invoked.

Explicit invocation includes any of:

- `@botname` mention in the message.
- `/<engine-id>` or `/<project-alias>` as the first token.
- Replying to a bot message.
- Built-in or plugin slash commands (for example `/agent`, `/model`, `/reasoning`, `/file`, `/trigger`).

Note: In forum topics, some Telegram clients include `reply_to_message` on every
message, pointing at the topic’s root service message (`message_id ==
message_thread_id`). Takopi treats those as implicit topic references, not
explicit replies, so they do not trigger mentions-only mode.

Commands:

- `/trigger` shows the current mode and defaults.
- `/trigger mentions` restricts runs to explicit invocations.
- `/trigger all` restores the default behavior.
- `/trigger clear` clears a topic override (topics only).

In group chats, changing trigger mode requires the sender to be an admin.

State is stored in `telegram_chat_prefs_state.json` (chat default) and
`telegram_topics_state.json` (topic overrides) alongside the config file.

### Forwarded message coalescing

Telegram sends a "comment + forwards" burst as separate messages, with the comment
arriving first. Takopi waits briefly so it can attach the forwarded messages and
run once.

Behavior:

- When a prompt candidate arrives, Takopi waits for `forward_coalesce_s` seconds
  of quiet for that sender + chat/topic.
- Forwarded messages arriving during the window are appended to the prompt
  (separated by blank lines) and do not start their own runs.
- Forwarded messages by themselves do not start runs.

Configuration (under `[transports.telegram]`):

=== "takopi config"

    ```sh
    takopi config set transports.telegram.forward_coalesce_s 1.0
    ```

=== "toml"

    ```toml
    forward_coalesce_s = 1.0 # set 0 to disable the delay
    ```

## Chat sessions (optional)

If you chose the **handoff** workflow during onboarding, Takopi uses stateless mode
where you reply to continue a session. The **assistant** and **workspace** workflows
use chat mode with auto-resume enabled.

Configuration (under `[transports.telegram]`):

=== "takopi config"

    ```sh
    takopi config set transports.telegram.show_resume_line true
    takopi config set transports.telegram.session_mode "chat"
    ```

=== "toml"

    ```toml
    show_resume_line = true # set false to hide resume lines
    session_mode = "chat" # or "stateless"
    ```

Behavior:

- Stores one resume token per engine per chat (per sender in group chats).
- Auto-resumes when no explicit resume token is present.
- Reply resume lines always take precedence and update the stored session for that engine.
- Reset with `/new`.

State is stored in `telegram_chat_sessions_state.json` alongside the config file.

Set `show_resume_line = false` to hide resume lines when takopi can auto-resume
(topics or chat sessions) and a project context is resolved. Otherwise the resume
line stays visible so reply-to-continue still works.

## Message overflow

By default, takopi trims long final responses to ~3500 characters to stay under
Telegram's 4096 character limit after entity parsing. You can opt into splitting
instead:

=== "takopi config"

    ```sh
    takopi config set transports.telegram.message_overflow "split"
    ```

=== "toml"

    ```toml
    [transports.telegram]
    message_overflow = "split" # trim | split
    ```

Split mode sends multiple messages. Each chunk includes the footer; follow-up
chunks add a "continued (N/M)" header.

## Forum topics (optional)

If you chose the **workspace** workflow during onboarding, topics are already enabled.
Topics bind Telegram forum threads to a project/branch and persist resume tokens per
topic, so replies keep the right context even after restarts.

Configuration (under `[transports.telegram]`):

=== "takopi config"

    ```sh
    takopi config set transports.telegram.topics.enabled true
    takopi config set transports.telegram.topics.scope "auto"
    ```

=== "toml"

    ```toml
    [transports.telegram.topics]
    enabled = true
    scope = "auto" # auto | main | projects | all
    ```

Requirements:

- `main`: `chat_id` must be a forum-enabled supergroup (topics enabled).
- `projects`: each `projects.<alias>.chat_id` must point to a forum-enabled
  supergroup for that project.
- `all`: both the main chat and each project chat must be forum-enabled.
- `auto`: if any project chats are configured, uses `projects`; otherwise `main`.
- The bot needs the **Manage Topics** permission in the relevant chat(s).

Commands:

- `main`: `/topic <project> @branch` creates a topic in the main chat and binds it.
- `projects`: `/topic @branch` creates a topic in the project chat and binds it.
- `all`: use `/topic <project> @branch` in the main chat, or `/topic @branch` in
  project chats.
- `/ctx` shows the bound context and stored session engines inside topics.
  Outside topics, `/ctx set ...` and `/ctx clear` bind the chat context.
- `/new` inside a topic clears stored resume tokens for that topic.

State is stored in `telegram_topics_state.json` alongside the config file.
Delete it to reset all topic bindings and stored sessions.

Note: main chat topics do not assume a default project; topics must be bound
before running without directives.

## Outbox model

- Single worker processes one op at a time.
- Each op is keyed; only one pending op per key.
- New ops with the same key overwrite the payload but **do not** reset
  `queued_at` (fairness).

Keys (include `chat_id` to avoid cross-chat collisions):

- `("edit", chat_id, message_id)` for edits (coalesced).
- `("delete", chat_id, message_id)` for deletes.
- `("send", chat_id, replace_message_id)` when replacing a progress message.
- Unique key for normal sends.

Scheduling:

- Ordered by `(priority, queued_at)`.
- Priorities: send=0, delete=1, edit=2.
- Within a priority tier, the oldest pending op runs first.

## Rate limiting + backoff

- Per-chat pacing is computed from `private_chat_rps` and `group_chat_rps`.
  Defaults: 1.0 msg/s for private, 20/60 msg/s for groups (≈1 message every 3s).
- Pacing is currently enforced via a single global `next_at`; per-chat
  `next_at` is a future consideration if we ever run multiple chats in parallel.
- The worker waits until `max(next_at, retry_at)` before executing the next op.
- On 429, `RetryAfter` is raised using `parameters.retry_after` when present;
  if missing, we fall back to a 5s delay. The outbox sets `retry_at` and
  requeues the op if no newer op for the same key has arrived.

## Error handling

- Non-429 errors are logged and dropped (no retry).
- On `RetryAfter`, the op is retried unless a newer op superseded the same key.

## Replace progress messages

`send_message(replace_message_id=...)`:

- Drops any pending edit for that progress message.
- Enqueues the send at highest priority.
- If the send succeeds, enqueues a delete for the old progress message.

This keeps the final message first and avoids deleting progress if the send
fails.

## getUpdates

`get_updates` bypasses the outbox and retries on `RetryAfter` by sleeping
for the provided delay.

## Close semantics

`TelegramClient.close()` shuts down the outbox and closes the HTTP client.
Pending ops are failed with `None` (best-effort).
