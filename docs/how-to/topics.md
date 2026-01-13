# Topics

Topics bind Telegram forum threads to a specific project/branch context. They can also store resume tokens and a default agent per topic.

## Enable topics

```toml
[transports.telegram.topics]
enabled = true
scope = "auto" # auto | main | projects | all
```

Your bot needs **Manage Topics** permission in the group.

If any `projects.<alias>.chat_id` are configured, topics are managed in those project chats; otherwise topics are managed in the main chat.

## Topic commands

Run these inside a topic thread:

| Command | Description |
|---------|-------------|
| `/topic <project> @branch` | Create a new topic bound to context |
| `/ctx` | Show the current binding |
| `/ctx set <project> @branch` | Update the binding |
| `/ctx clear` | Remove the binding |
| `/new` | Clear stored sessions for this topic |

In project chats, omit the project: `/topic @branch` or `/ctx set @branch`.

## Chat sessions

Chat sessions store one resume token per chat (per sender in groups) so new messages can auto-resume without replying.

Enable:

```toml
[transports.telegram]
session_mode = "chat" # stateless | chat
```

Reset the stored session with `/new`.

## State files

- Topic state: `telegram_topics_state.json`
- Chat sessions state: `telegram_chat_sessions_state.json`
- Chat defaults (e.g. `/agent`): `telegram_chat_prefs_state.json`

## Related

- [Switch engines](switch-engines.md)
- [Commands & directives](../reference/commands-and-directives.md)
