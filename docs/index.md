# Takopi

Takopi connects agent CLIs to Telegram so you can run, monitor, and reply to long-running tasks from chat.
It supports multiple runner backends and a pluggable transport layer, with a stable public API for extensions.

## What this site covers

- How to get Takopi running end-to-end
- Project aliases and worktree-aware workflows
- The plugin system and stable public API surface
- Architectural details and behavioral guarantees

## Quick start

```bash
uv run takopi --help
```

## Documentation map

- Start here: [User guide](user-guide.md)
- Projects and worktrees: [Projects](projects.md)
- Plugin development: [Plugins](plugins.md) and [Public API](public-api.md)
- System behavior: [Architecture](architecture.md) and [Specification](specification.md)
- Transport details: [Telegram](transports/telegram.md)
- Contributor notes: [Developing](developing.md)

## LLM entrypoints

- `/llms.txt` lists the key pages and links to their Markdown mirrors.
- `/llms-full.txt` contains the full expanded content of those pages.
