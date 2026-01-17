# Routing & sessions

Takopi supports both **stateless** and **chat** modes for session handling. In stateless mode, each message starts a new session unless you reply to continue. In chat mode, new messages auto-resume the previous session.

## Continuation (how threads persist)

Takopi supports three ways to continue a thread:

1. **Reply-to-continue** (always available)
   - Reply to any bot message that contains a resume line in the footer.
   - Takopi extracts the resume token and resumes that engine thread.
   - Reply resume lines always take precedence over chat sessions or topic storage.
   - The resumed run updates the stored session for that engine when the token is known.
2. **Forum topics** (optional)
   - Topics can store resume tokens per topic and auto-resume new messages in that topic.
   - Topic state is stored in `telegram_topics_state.json`.
   - Reset with `/new`.
3. **Chat sessions** (optional)
   - Set `session_mode = "chat"` to store one resume token per chat (per sender in groups).
   - Stored sessions are per engine; resuming a different engine does not overwrite others.
   - State is stored in `telegram_chat_sessions_state.json`.
   - Reset with `/new`.

Reply-to-continue works even if topics or chat sessions are enabled.

## Routing (how Takopi picks a runner)

For each message, Takopi:

- parses directive prefixes (`/<engine-id>`, `/<project-alias>`, `@branch`) from the first non-empty line
- attempts to extract a resume token by polling available runners
- if a resume token is found, routes to the matching runner; otherwise uses the configured default engine

## Serialization (why you don’t get overlapping runs)

Takopi allows parallel runs across **different threads**, but enforces serialization within a thread:

- Telegram side: jobs are queued FIFO per thread.
- Runner side: runners enforce per-resume-token locks (so the same session can’t be resumed concurrently).

The precise invariants are specified in the [Specification](../reference/specification.md).

## Related

- [Conversation modes](../tutorials/conversation-modes.md)
- [Chat sessions](../how-to/chat-sessions.md)
- [Commands & directives](../reference/commands-and-directives.md)
- [Context resolution](../reference/context-resolution.md)
