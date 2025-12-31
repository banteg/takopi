# changelog

## v0.2.0 (2025-12-31)

### changes

- refactor runner with takopi event normalization and protocol contract #8
- migrate async runtime from asyncio to anyio #6
- add `/cancel` command with progress message targeting #4
- stream runner events via async iterators (natural backpressure)
- render resume as `` `codex resume <token>` `` command lines
- cap active bridge runs at 16 with per-thread backlog management
- emit `completed` as the terminal event (resume + final answer)
- remove `--profile` flag; configure via `[codex].profile` only
- require python 3.14+

### fixes

- preserve resume tokens in error renders #3
- preserve file-change paths in action events #2
- terminate codex process groups on cancel (POSIX)
- serialize new sessions once resume token is known
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
