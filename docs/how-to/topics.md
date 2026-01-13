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

## State files

Topic state is stored in `telegram_topics_state.json` next to your config file. Chat defaults live in `telegram_chat_prefs_state.json`.

## Related

- [Switch engines](switch-engines.md)
- [Commands & directives](../reference/commands-and-directives.md)

