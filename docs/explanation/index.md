# Explanation

Explanation docs answer **“how does this work?”** and **“why is it designed this way?”**

If you want step-by-step instructions, go to **[Tutorials](../tutorials/index.md)**.  
If you want exact options and contracts, go to **[Reference](../reference/index.md)**.

## How Takopi works end-to-end

- Incoming Telegram message → resolve context (project/branch) → resolve resume token → select runner → stream events → render progress → send final + resume line.

Start here:

- [Architecture](architecture.md)

## Routing, sessions, and continuation

Takopi is stateless by default, but can provide “continuation” in multiple ways:

- reply-to-continue (always available)
- per-topic resume (Telegram forum topics)
- per-chat sessions (auto-resume)

- [Routing & sessions](routing-and-sessions.md)

## Plugins and extensibility

Takopi uses entrypoint-based plugins with lazy discovery so broken plugins don’t brick the CLI.

- [Plugin system](plugin-system.md)

## Codebase orientation

If you’re making changes, this is the “map of the territory”:

- [Module map](module-map.md)

## Where to look for hard rules

Explanation pages describe intent and tradeoffs. The *hard requirements* live in:

- [Reference: Specification](../reference/specification.md)
- [Reference: Plugin API](../reference/plugin-api.md)
