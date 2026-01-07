# changelog

## v0.9.0 (unreleased)

### breaking

- remove `takopi.bridge`; Telegram bridge now lives in `takopi.bridges.telegram`

### changes

- add transport/presenter protocols plus transport-agnostic `exec_bridge`
- move Telegram polling + wiring into `takopi.bridges.telegram` with transport/presenter adapters
- add project configuration, directive parsing (`/project`, `@branch`), and `ctx:`-aware routing for runs
- add `takopi init` to register project aliases from the main checkout (with worktree defaults)
- resolve git worktrees on demand and run engine subprocesses in the project/worktree cwd
- list configured projects in the startup banner
- add a shared incoming message shape plus Telegram parsing helpers

### fixes

- render `ctx:` footer lines consistently (backticked + hard breaks) and include them in final messages

### docs

- add a projects/worktrees guide and document `takopi init` behavior in the README

## v0.8.0 (2026-01-05)

### changes

- queue telegram requests with rate limits and retry-after backoff [#54](https://github.com/banteg/takopi/pull/54)

### docs

- improve documentation coverage [#52](https://github.com/banteg/takopi/pull/52)
- align runner guide with factory pattern
- add missing pr links in the changelog

## v0.7.0 (2026-01-04)

### changes

- migrate logging to structlog with structured pipelines and redaction [#46](https://github.com/banteg/takopi/pull/46)
- add msgspec schemas for jsonl decoding across runners [#37](https://github.com/banteg/takopi/pull/37)

## v0.6.0 (2026-01-03)

### changes

- interactive onboarding: run `takopi` to set up bot token, chat id, and default engine via guided prompts [#39](https://github.com/banteg/takopi/pull/39)
- lockfile to prevent multiple takopi instances from racing the same bot token [#30](https://github.com/banteg/takopi/pull/30)
- re-run onboarding anytime with `takopi --onboard`

## v0.5.3 (2026-01-02)

### changes

- default claude allowed tools to `["Bash", "Read", "Edit", "Write"]` when not configured [#29](https://github.com/banteg/takopi/pull/29)

## v0.5.2 (2026-01-02)

### changes

- show not installed agents in the startup banner (while hiding them from slash commands)

### fixes

- treat codex reconnect notices as non-fatal progress updates instead of errors [#27](https://github.com/banteg/takopi/pull/27)
- avoid crashes when codex tool/file-change events omit error fields [#27](https://github.com/banteg/takopi/pull/27)

## v0.5.1 (2026-01-02)

### changes

- relax telegram ACL to check chat id only, enabling use in group chats and channels [#26](https://github.com/banteg/takopi/pull/26)
- improve onboarding documentation and add tests [#25](https://github.com/banteg/takopi/pull/25)

## v0.5.0 (2026-01-02)

### changes

- add an opencode runner via the `opencode` CLI with json event parsing and resume support [#22](https://github.com/banteg/takopi/pull/22)
- add a pi agent runner via the `pi` CLI with jsonl streaming and resume support [#24](https://github.com/banteg/takopi/pull/24)
- document the opencode and pi runners, event mappings, and stream capture tips

### fixes

- fix path relativization so progress output does not strip sibling directories [#23](https://github.com/banteg/takopi/pull/23)
- reduce noisy debug logging from markdown_it/httpcore

## v0.4.0 (2026-01-02)

### changes

- add auto-router runner selection with configurable default engine [#15](https://github.com/banteg/takopi/pull/15)
- make auto-router the default entrypoint; subcommands or `/{engine}` prefixes override for new threads
- add `/cancel` + `/{engine}` command menu sync on startup
- show engine name in progress and final message headers
- omit progress/action log lines from final output for cleaner answers [#21](https://github.com/banteg/takopi/pull/21)

### fixes

- improve codex exec error rendering with stderr extraction [#18](https://github.com/banteg/takopi/pull/18)
- preserve markdown formatting and resume footer when trimming long responses [#20](https://github.com/banteg/takopi/pull/20)

## v0.3.0 (2026-01-01)

### changes

- add a claude code runner via the `claude` CLI with stream-json parsing and resume support [#9](https://github.com/banteg/takopi/pull/9)
- auto-discover engine backends and generate CLI subcommands from the registry [#12](https://github.com/banteg/takopi/pull/12)
- add `BaseRunner` session locking plus a `JsonlSubprocessRunner` helper for jsonl subprocess engines
- add jsonl stream parsing and subprocess helpers for runners
- lazily allocate per-session locks and streamline backend setup/install metadata
- improve startup message formatting and markdown rendering
- add a debug onboarding helper for setup troubleshooting

### breaking

- runner implementations must define explicit resume parsing/formatting (no implicit standard resume pattern)

### fixes

- stop leaking a hidden `engine-id` CLI option on engine subcommands

### docs

- add a runner guide plus Claude Code docs (runner, events, stream-json cheatsheet)
- clarify the Claude runner file layout and add guidance for JSONL-based runners
- document “minimal” runner mode: Started+Completed only, completed-only actions allowed

## v0.2.0 (2025-12-31)

### changes

- introduce runner protocol for multi-engine support [#7](https://github.com/banteg/takopi/pull/7)
  - normalized event model (`started`, `action`, `completed`)
  - actions with stable ids, lifecycle phases, and structured details
  - engine-agnostic bridge and renderer
- add `/cancel` command with progress message targeting [#4](https://github.com/banteg/takopi/pull/4)
- migrate async runtime from asyncio to anyio [#6](https://github.com/banteg/takopi/pull/6)
- stream runner events via async iterators (natural backpressure)
- per-thread job queues with serialization for same-thread runs
- render resume as `codex resume <token>` command lines
- various rendering improvements including file edits

### breaking

- require python 3.14+
- remove `--profile` flag; configure via `[codex].profile` only

### fixes

- serialize new sessions once resume token is known
- preserve resume tokens in error renders [#3](https://github.com/banteg/takopi/pull/3)
- preserve file-change paths in action events [#2](https://github.com/banteg/takopi/pull/2)
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
