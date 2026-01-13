# Agent quickstart

This section is written for LLM agents and humans collaborating with agents.

## Commands agents should prefer

```bash
uv run pytest
uv run ruff check src tests
uv run ty check src tests
```

## Where the stable interfaces are

- **Public plugin API:** `takopi.api` (see [Public API](../public-api.md))
- Everything else is internal and may change.

## How to navigate the codebase

See [Repo map](repo-map.md).
