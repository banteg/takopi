# Conversation modes

Takopi can handle follow-up messages in two ways: **chat mode** (auto-resume) or **stateless** (reply-to-continue). Pick the one that matches how you want Telegram to feel.

## Quick pick

- **Choose chat mode** if you want a normal chat flow where new messages continue the same thread.
- **Choose stateless** if you want every message to start clean unless you explicitly reply.

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

Tip: set a default agent for this chat with `/agent set claude`.

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

## Where to set it

Onboarding will ask you, or you can set it in config:

```toml
[transports.telegram]
session_mode = "chat" # or "stateless"
show_resume_line = false # optional, see below
```

## Resume lines in chat mode

If you enable chat mode (or topics), Takopi can auto-resume, so you can hide resume lines for a cleaner chat.
Resume lines are still shown when no project context is set, so replies can branch there.

If you prefer always-visible resume lines, set:

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
