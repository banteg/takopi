# Route by chat

Bind a Telegram chat to a project so messages in that chat automatically route to the right repo.

## Capture a chat id and save it to a project

Run:

```sh
takopi chat-id --project happy-gadgets
```

Then send any message in the target chat. Takopi captures the `chat_id` and updates your config:

=== "takopi config"

    ```sh
    takopi config set projects.happy-gadgets.path "~/dev/happy-gadgets"
    takopi config set projects.happy-gadgets.chat_id -1001234567890
    ```

=== "toml"

    ```toml
    [projects.happy-gadgets]
    path = "~/dev/happy-gadgets"
    chat_id = -1001234567890
    ```

Messages from that chat now default to the project.

## Rules for chat ids

- Each `projects.*.chat_id` must be unique.
- A project `chat_id` must not match `transports.telegram.chat_id`.
- Telegram uses positive IDs for private chats and negative IDs for groups/supergroups.

## Capture a chat id without saving

```sh
takopi chat-id
```

## Related

- [Topics](topics.md)
- [Context resolution](../reference/context-resolution.md)
