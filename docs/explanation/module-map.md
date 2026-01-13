# Module map

This page is a high-level map of Takopi’s internal modules: what they do and how they fit together.

## Entry points

| Module | Responsibility |
|--------|----------------|
| `cli.py` | Typer CLI entry point; loads settings, selects engine/transport, runs the transport backend. |
| `telegram/backend.py` | Telegram transport backend: validates config, runs onboarding, builds and runs the Telegram bridge. |

## Orchestration and routing

| Module | Responsibility |
|--------|----------------|
| `runner_bridge.py` | Transport-agnostic orchestration: per-message handler, progress updates, final render, cancellation, resume coordination. |
| `router.py` | Auto-router: resolves resume tokens by polling runners; selects a runner for a message. |
| `scheduler.py` | Per-thread FIFO job queueing with serialization. |
| `transport_runtime.py` | Facade used by transports and commands to resolve messages and runners without importing internal router/project types. |

## Domain model and events

| Module | Responsibility |
|--------|----------------|
| `model.py` | Domain types: resume tokens, events, actions, run results. |
| `runner.py` | Runner protocol and event queue utilities. |
| `events.py` | Event factory helpers for building Takopi events consistently. |

## Rendering and progress

| Module | Responsibility |
|--------|----------------|
| `progress.py` | Progress tracking: reduces takopi events into progress snapshots. |
| `markdown.py` | Markdown formatting for progress/final messages; includes helpers like elapsed formatting. |
| `presenter.py` | Presenter protocol: converts `ProgressState` into transport-specific messages. |
| `transport.py` | Transport protocol: send/edit/delete abstractions and message reference types. |

## Telegram implementation

| Module | Responsibility |
|--------|----------------|
| `telegram/bridge.py` | Telegram bridge loop: polls updates, filters messages, dispatches handlers, coordinates cancellation. |
| `telegram/client.py` | Telegram API wrapper with retry/outbox semantics. |
| `telegram/render.py` | Telegram markdown rendering and trimming. |
| `telegram/onboarding.py` | Interactive setup and setup validation UX. |
| `telegram/commands/*` | In-chat command handlers (`/agent`, `/file`, `/topic`, `/ctx`, `/new`, …). |

## Plugins

| Module | Responsibility |
|--------|----------------|
| `plugins.py` | Entrypoint discovery and lazy loading (capture load errors, filter by enabled list). |
| `engines.py` | Engine backend discovery and loading via entrypoints. |
| `transports.py` | Transport backend discovery and loading via entrypoints. |
| `commands.py` | Command backend discovery and loading via entrypoints; command execution helpers. |
| `ids.py` | Shared ID regex and collision checks for plugin ids and Telegram command names. |
| `api.py` | Public plugin API boundary (`takopi.api` re-exports). |

## Runners and schemas

| Module | Responsibility |
|--------|----------------|
| `runners/*` | Engine runner implementations (Codex, Claude, OpenCode, Pi). |
| `schemas/*` | msgspec schemas / decoders for engine JSONL streams. |

## Configuration and persistence

| Module | Responsibility |
|--------|----------------|
| `settings.py` | Loads `takopi.toml` (TOML + env), validates with pydantic-settings. |
| `config_store.py` | Raw TOML read/write (merge/update without clobbering extra sections). |
| `config_migrations.py` | One-time edits to on-disk config (e.g. legacy Telegram key migration). |

## Utilities

| Module | Responsibility |
|--------|----------------|
| `utils/paths.py` | Path/command relativization helpers. |
| `utils/streams.py` | Async stream helpers (`iter_bytes_lines`, stderr draining). |
| `utils/subprocess.py` | Subprocess management helpers (terminate/kill best-effort). |

