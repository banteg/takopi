# Voice Transcript Reaction Plan

## Goal

When a Telegram user sends a voice message and Takopi successfully transcribes it, post a visible transcript reply so the text appears in chat history. The reply should be silent (no notification) and trimmed to Telegram limits.

## Current behavior (trace)

- `src/takopi/telegram/loop.py` handles incoming updates and routes messages.
- For voice messages, `route_message()` calls `transcribe_voice()`.
- `src/takopi/telegram/voice.py:transcribe_voice()` downloads the voice payload, calls OpenAI, and returns the transcript text.
- On success, the returned text is only used for prompting. There is no user-visible message for the transcript.
- Errors use the `reply` callback to respond with a short error message, and the run is skipped.

## Architecture notes (handoff-friendly)

- The Telegram transport uses:
  - `TelegramBridgeConfig` (runtime/config wiring) in `src/takopi/telegram/bridge.py`.
  - `TelegramTransport` to send/edit/delete messages.
  - `TelegramPresenter` + `MarkdownFormatter` to render progress/final messages.
- Progress/final rendering is separate from ad-hoc replies; ad-hoc replies go through `send_plain()` which renders only a header via `MarkdownParts(header=...)`.
- Telegram markdown rendering and trimming is centralized in `src/takopi/telegram/render.py`:
  - `prepare_telegram()` trims body to `MAX_BODY_CHARS` and returns `(text, entities)`.
- Commands, directives, and resume routing are resolved before runs are scheduled.
- The bot often sends multiple replies to the same user message:
  - an initial progress message (notify=False)
  - a final answer (notify depends on config)
  - additional ad-hoc replies (commands/errors)

## User choices (explicit)

- Transcript display: reply message to the voice note
- Notifications: silent (no push)
- Long transcripts: trim to Telegram limits (no splitting)

## Proposed solution (minimal, aligned with existing patterns)

1. Add a helper in `src/takopi/telegram/bridge.py` for transcript replies.
   - Use `MarkdownParts(header="voice transcript", body=<transcript>)`.
   - Render with `prepare_telegram()` so we get trimming and entities.
   - Send via `Transport.send()` with `SendOptions(reply_to=MessageRef(...), notify=False, thread_id=...)`.
   - Guard against empty/whitespace-only transcripts.

2. Call the helper from `route_message()` in `src/takopi/telegram/loop.py` right after `transcribe_voice()` succeeds and before the prompt is dispatched.
   - This ensures the transcript appears in chat history as soon as it is ready.
   - Keep existing `(voice transcribed)` prefix in the prompt for the run.

## Test plan

- Update `tests/test_telegram_bridge.py::test_run_main_loop_voice_transcript_preserves_directive`:
  - Assert an extra `FakeTransport.send_calls` entry exists for the transcript reply.
  - Validate:
    - Reply target is the original voice message.
    - `notify` is `False`.
    - Message contains the transcript text and header.
- Add a focused test for trimming (same file):
  - Use a long transcript and assert the sent text ends with `...` or includes the ellipsis from `prepare_telegram()`.

## Edge cases / risks

- Mentions-only trigger mode: voice messages are skipped entirely, so no transcript reply (consistent with trigger rules).
- Markdown rendering: transcript text will be parsed by MarkdownIt. If this is undesirable, wrap the transcript in a code block in the helper (optional follow-up).
- Multiple replies to the same voice message are expected (transcript + progress + final).

## Implementation steps

1. Add `send_voice_transcript_reply()` (or similar) in `src/takopi/telegram/bridge.py`.
2. Call it from `src/takopi/telegram/loop.py` after a successful transcription.
3. Update tests in `tests/test_telegram_bridge.py`.

## Follow-up ideas (optional)

- Config toggle for transcript replies (per transport).
- Use code blocks or blockquotes to preserve transcript formatting.
- Suppress transcript reply when transcript is very short (configurable threshold).
