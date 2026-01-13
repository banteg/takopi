# First run

You’ll run Takopi in a repo, send a task from Telegram, and learn the basic controls.

## 1) Start Takopi in a repo

```sh
cd ~/dev/your-repo
takopi
```

## 2) Send a message to your bot

Takopi streams progress in chat and posts a final message when the engine finishes.

If you want to override the default engine for a single message, prefix the first line:

```
/codex explain this code
/claude refactor this module
```

## 3) Continue or cancel

- Continue the same thread by **replying** to any bot message that contains a resume line in the footer.
- Cancel an in-flight run by clicking the cancel button or replying to the progress message with `/cancel`.

## Next

- [Projects](../how-to/projects.md) and [Worktrees](../how-to/worktrees.md)
- [Commands & directives](../reference/commands-and-directives.md)

