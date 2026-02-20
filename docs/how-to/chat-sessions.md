# Chat sessions

Chat sessions store one resume token per engine per chat (per sender in group chats), so new messages can auto-resume without replying. Reply-to-continue still works and updates the stored session for that engine.

!!! tip "Assistant and workspace workflows"
    If you chose **assistant** or **workspace** during [onboarding](../tutorials/install.md), chat sessions are already enabled. This guide covers how they work and how to customize them.

## Enable chat sessions

If you chose **handoff** during onboarding and want to switch to chat mode:

=== "takopi config"

    ```sh
    takopi config set transports.telegram.session_mode "chat"
    ```

=== "toml"

    ```toml
    [transports.telegram]
    session_mode = "chat" # stateless | chat
    ```

With `session_mode = "chat"`, new messages in the chat continue the current thread automatically.

## Reset a session

Use `/new` to clear the stored session for the current scope:

- In a private chat, it resets the chat.
- In a group, it resets **your** session in that chat.
- In a forum topic, it resets the topic session.

See `/new` in [Commands & directives](../reference/commands-and-directives.md).

## Resume lines and branching

Chat sessions do not remove reply-to-continue. If resume lines are visible, you can reply to any older message to branch the conversation.

If you prefer a cleaner chat, hide resume lines:

=== "takopi config"

    ```sh
    takopi config set transports.telegram.show_resume_line false
    ```

=== "toml"

    ```toml
    [transports.telegram]
    show_resume_line = false
    ```

## How it behaves in groups

In group chats, Takopi stores a session per sender, so different people can work independently in the same chat.

## Working directory changes

When `session_mode = "chat"` is enabled, Takopi clears stored chat sessions on startup if the current working directory differs from the one recorded in `telegram_chat_sessions_state.json`. This avoids resuming directory-bound sessions from a different project.

## Related

- [Conversation modes](../tutorials/conversation-modes.md)
- [Forum topics](topics.md)
- [Commands & directives](../reference/commands-and-directives.md)
