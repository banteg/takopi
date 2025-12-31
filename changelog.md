# changelog

## v0.2.0 (2025-12-31)

### changes

- introduce runner protocol for multi-engine support #8
  - normalized event model (`started`, `action`, `completed`)
  - actions with stable ids, lifecycle phases, and structured details
  - engine-agnostic bridge and renderer
- add `/cancel` command with progress message targeting #4
- migrate async runtime from asyncio to anyio #6
- stream runner events via async iterators (natural backpressure)
- per-thread job queues with serialization for same-thread runs
- emit `completed` as terminal event (carries resume + final answer)
- render resume as `` `codex resume <token>` `` command lines

### breaking

- require python 3.14+
- remove `--profile` flag; configure via `[codex].profile` only

### fixes

- serialize new sessions once resume token is known
- preserve resume tokens in error renders #3
- preserve file-change paths in action events #2
- terminate codex process groups on cancel (POSIX)
- correct resume command matching in bridge

## v0.1.0 (2025-12-29)

### features

- telegram bot bridge for openai codex cli via `codex exec`
- stateless session resume via `` `codex resume <token>` `` lines
- real-time progress updates with ~2s throttling
- full markdown rendering with telegram entities (markdown-it-py + sulguk)
- per-session serialization to prevent race conditions
- interactive onboarding guide for first-time setup
- codex profile configuration
- automatic telegram token redaction in logs
- cli options: `--debug`, `--final-notify`, `--version`
