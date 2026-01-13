# Repo map

Quick pointers for navigating the Takopi codebase.

## Where things start

- CLI entry point: `src/takopi/cli.py`
- Telegram backend entry point: `src/takopi/telegram/backend.py`
- Telegram bridge loop: `src/takopi/telegram/bridge.py`
- Transport-agnostic handler: `src/takopi/runner_bridge.py`

## Core concepts

- Domain types (resume tokens, events, actions): `src/takopi/model.py`
- Runner protocol: `src/takopi/runner.py`
- Router selection and resume polling: `src/takopi/router.py`
- Per-thread scheduling: `src/takopi/scheduler.py`
- Progress reduction and rendering: `src/takopi/progress.py`, `src/takopi/markdown.py`

## Engines and streaming

- Runner implementations: `src/takopi/runners/*`
- JSONL decoding schemas: `src/takopi/schemas/*`

## Plugins

- Public API boundary (`takopi.api`): `src/takopi/api.py`
- Entrypoint discovery + lazy loading: `src/takopi/plugins.py`
- Engine/transport/command backend loading: `src/takopi/engines.py`, `src/takopi/transports.py`, `src/takopi/commands.py`

## Configuration

- Settings model + TOML/env loading: `src/takopi/settings.py`
- Config migrations: `src/takopi/config_migrations.py`

## Docs and contracts

- Normative behavior: [Specification](../specification.md)
- Runner invariants: `tests/test_runner_contract.py`

