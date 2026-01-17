# Conversation modes

Takopi can handle follow-up messages in two ways: **chat mode** (auto-resume) or **stateless** (reply-to-continue).

During [onboarding](install.md), you chose a **workflow** (assistant, workspace, or handoff) that automatically configured this for you:

| Workflow | Session mode | Topics | Resume lines |
|----------|--------------|--------|--------------|
| **assistant** | chat | off | hidden |
| **workspace** | chat | on | hidden |
| **handoff** | stateless | off | shown |

This page explains what those settings mean and how to change them.

## Chat mode (auto-resume)

**What it feels like:** a normal chat assistant.

!!! user "You"
    explain what this repo does

!!! takopi "Takopi"
    done · codex · 8s
    ...

!!! user "You"
    now add tests

Takopi treats the second message as a continuation. If you want a clean slate, use:

!!! user "You"
    /new

To pin a project or branch for the chat, use:

!!! user "You"
    /ctx set <project> [@branch]

`/new` clears the session but keeps the bound context.

Tip: set a default engine for this chat with `/agent set claude`.

## Stateless (reply-to-continue)

**What it feels like:** every message is independent until you reply.

!!! user "You"
    explain what this repo does

!!! takopi "Takopi"
    done · codex · 8s
    ...
    codex resume abc123

To continue the same session, **reply** to a message with a resume line:

!!! takopi "Takopi"
    done · codex · 8s

    !!! user "You"
        now add tests

## Changing your settings

You can manually change these settings in your config file:

=== "takopi config"

    ```sh
    takopi config set transports.telegram.session_mode "chat"
    takopi config set transports.telegram.show_resume_line false
    ```

=== "toml"

    ```toml
    [transports.telegram]
    session_mode = "chat"      # "chat" or "stateless"
    show_resume_line = false   # true or false
    ```

Or re-run onboarding to pick a different workflow:

```sh
takopi --onboard
```

## Resume lines in chat mode

If you enable chat mode (or topics), Takopi can auto-resume, so you can hide resume lines for a cleaner chat.
Disable them if you want a fully clean footer, or enable `show_resume_line` to keep reply-branching visible.

If you prefer always-visible resume lines, set:

=== "takopi config"

    ```sh
    takopi config set transports.telegram.show_resume_line true
    ```

=== "toml"

    ```toml
    [transports.telegram]
    show_resume_line = true
    ```

## Reply-to-continue still works

Even in chat mode, replying to a message with a resume line takes precedence and branches from that point.

## Related

- [Routing and sessions](../explanation/routing-and-sessions.md)
- [Chat sessions](../how-to/chat-sessions.md)
- [Forum topics](../how-to/topics.md)
- [Commands & directives](../reference/commands-and-directives.md)

## Next

Now that you know which mode you want, move on to your first run:

[First run →](first-run.md)
