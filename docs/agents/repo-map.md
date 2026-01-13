# Repo map

## Entry points

- `src/takopi/cli.py` — Typer CLI entry point
- `src/takopi/api.py` — public plugin API exports

## Core orchestration

- `src/takopi/runner_bridge.py` — transport-agnostic orchestration

## Transports

- `src/takopi/telegram/*` — Telegram backend/bridge/presenter

## Contracts/tests

- `tests/test_runner_contract.py` — event ordering contract for runners
