# changelog

## v0.2.0 (2025-12-31)

### highlights

- codex runner refactor with takopi event normalization (`session.started`, `action.*`, `log`, `error`)
- resume command lines: `` `codex resume <token>` ``
- `/cancel` support via progress message id + AnyIO cancel scopes
- ordered event sink delivery via a single drain task (no per-event tasks)

### changes

- align codebase to v0.2.0 spec (domain model, runner protocol, module split)
- normalize event schema (session titles, action `ok`, error `detail`, log levels)
- enforce per-thread serialization for new sessions and abort on event callback errors
- bridge queueing caps active runs at 16 while keeping per-thread backlogs out of the limit
- add runner contract, serialization, and renderer/bridge coverage
- remove `--profile`; configure Codex profiles via `[codex].profile` only
- `RunResult` now carries only `resume` and `answer`

### fixes

- preserve resume tokens in error renders
- terminate codex process groups on cancel (POSIX) and keep bounded stderr tails
- handle worker shutdown cleanly on stream close
- align docs with the current runner / event architecture

## v0.1.0 (2025-12-29)

initial release.

### features

- telegram bot bridge for openai codex cli using `codex exec` and `codex exec resume`
- stateless session resume via `` `codex resume <token>` `` lines embedded in messages
- real-time progress updates with ~2s throttling, showing commands, tools, and elapsed time
- full markdown rendering with telegram entity support (via markdown-it-py + sulguk)
- concurrent message handling with per-session serialization to prevent race conditions
- automatic telegram token redaction in logs
- interactive onboarding guide for first-time setup
- cli options: `--profile`, `--debug`, `--final-notify`, `--version`
