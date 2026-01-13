# changelog

## v0.18.0 (2026-01-13)

### changes

- add per-chat and per-topic default agent via `/agent set` command [#109](https://github.com/banteg/takopi/pull/109)
- add session resume shorthand for pi runner [#113](https://github.com/banteg/takopi/pull/113)
- expose `sender_id` and `raw` fields on `MessageRef` for plugins [#112](https://github.com/banteg/takopi/pull/112)

### fixes

- recreate stale topic bindings when topic is deleted and recreated [#127](https://github.com/banteg/takopi/pull/127)
- use stdout session header for pi runner [#126](https://github.com/banteg/takopi/pull/126)

### docs

- restructure docs into diataxis format and switch to zensical [#121](https://github.com/banteg/takopi/pull/121) [#125](https://github.com/banteg/takopi/pull/125)

## v0.17.1 (2026-01-12)

### fixes

- fix telegram /new command crash [#106](https://github.com/banteg/takopi/pull/106)
- track telegram sessions for plugin runs [#107](https://github.com/banteg/takopi/pull/107)
- align telegram prompt upload resume flow [#105](https://github.com/banteg/takopi/pull/105)

## v0.17.0 (2026-01-12)

### changes

- add chat session mode (`session_mode = "chat"`) for auto-resume per chat without replying, reset with `/new` [#102](https://github.com/banteg/takopi/pull/102)
- add `message_overflow = "split"` to send long responses as multiple messages instead of trimming [#101](https://github.com/banteg/takopi/pull/101)
- add `show_resume_line` option to hide resume lines when auto-resume is available [#100](https://github.com/banteg/takopi/pull/100)
- add `auto_put_mode = "prompt"` to start a run with the caption after uploading a file [#97](https://github.com/banteg/takopi/pull/97)
- expose `thread_id` to plugins via run context [#99](https://github.com/banteg/takopi/pull/99)
- use tomli-w for config serialization [#103](https://github.com/banteg/takopi/pull/103)
- add `voice_transcription_model` setting for local whisper servers [#98](https://github.com/banteg/takopi/pull/98)

### docs

- document chat sessions, message overflow, and voice transcription model settings

## v0.16.0 (2026-01-12)

### fixes

- harden telegram file transfer handling [#84](https://github.com/banteg/takopi/pull/84)

### changes

- simplify runtime, config, and telegram internals [#85](https://github.com/banteg/takopi/pull/85)
- refactor telegram boundary types [#90](https://github.com/banteg/takopi/pull/90)

### docs

- add tips section to user guide
- rework readme

## v0.15.0 (2026-01-11)

### changes

- add telegram file transfer support [#83](https://github.com/banteg/takopi/pull/83)

### docs

- document telegram file transfers [#83](https://github.com/banteg/takopi/pull/83)

## v0.14.1 (2026-01-10)

### changes

- add topic scope and thread-aware replies for telegram topics [#81](https://github.com/banteg/takopi/pull/81)

### docs

- update telegram topics docs and user guide for topic scoping [#81](https://github.com/banteg/takopi/pull/81)

## v0.14.0 (2026-01-10)

### changes

- add telegram forum topics support with `/topic` command for binding threads to projects/branches, persistent resume tokens per topic, and `/ctx` for inspecting or updating bindings [#80](https://github.com/banteg/takopi/pull/80)
- add inline cancel button to progress messages [#79](https://github.com/banteg/takopi/pull/79)
- add config hot-reload via watchfiles [#78](https://github.com/banteg/takopi/pull/78)

### docs

- add user guide and telegram topics documentation [#80](https://github.com/banteg/takopi/pull/80)

## v0.13.0 (2026-01-09)

### changes

- add per-project chat routing [#76](https://github.com/banteg/takopi/pull/76)

### fixes

- hardcode codex exec flags [#75](https://github.com/banteg/takopi/pull/75)
- reuse project root for current branch when resolving worktrees [#77](https://github.com/banteg/takopi/pull/77)

### docs

- normalize casing in the readme and changelog

## v0.12.0 (2026-01-09)

### changes

- add optional telegram voice note transcription (routes transcript like typed text) [#74](https://github.com/banteg/takopi/pull/74)

### fixes

- fix plugin allowlist matching and windows session paths [#72](https://github.com/banteg/takopi/pull/72)

### docs

- document telegram voice transcription settings [#74](https://github.com/banteg/takopi/pull/74)

## v0.11.0 (2026-01-08)

### changes

- add entrypoint-based plugins for engines/transports plus a `takopi plugins` command and public API docs [#71](https://github.com/banteg/takopi/pull/71)

### fixes

- create pi sessions under the run base dir [#68](https://github.com/banteg/takopi/pull/68)
- skip git repo checks for codex runs [#66](https://github.com/banteg/takopi/pull/66)

## v0.10.0 (2026-01-08)

### changes

- add transport registry with `--transport` overrides and a `takopi transports` command [#69](https://github.com/banteg/takopi/pull/69)
- migrate config loading to pydantic-settings and move telegram credentials under `[transports.telegram]` [#65](https://github.com/banteg/takopi/pull/65)
- include project aliases in the telegram slash-command menu with validation and limits [#67](https://github.com/banteg/takopi/pull/67)

### fixes

- validate worktree roots instead of treating nested paths as worktrees [#63](https://github.com/banteg/takopi/pull/63)
- harden onboarding with clearer config errors, safe backups, and refreshed command menu wording [#70](https://github.com/banteg/takopi/pull/70)

### docs

- add architecture and lifecycle diagrams
- call out the default worktrees directory [#64](https://github.com/banteg/takopi/pull/64)
- document the transport registry and onboarding changes [#69](https://github.com/banteg/takopi/pull/69)

## v0.9.0 (2026-01-07)

### projects and worktrees

- register repos with `takopi init <alias>` and target them via `/project` directives
- route runs to git worktrees with `@branch` — takopi resolves or creates worktrees automatically
- replies preserve context via `ctx: project @branch` footers, no need to repeat directives
- set `default_project` to skip the `/project` prefix entirely
- per-project `default_engine` and `worktree_base` configuration

### changes

- transport/presenter protocols plus transport-agnostic `exec_bridge`
- move telegram polling + wiring into `takopi.telegram` with transport/presenter adapters
- list configured projects in the startup banner

### fixes

- render `ctx:` footer lines consistently (backticked + hard breaks) and include them in final messages

### breaking

- remove `takopi.bridge`; use `takopi.runner_bridge` and `takopi.telegram` instead

### docs

- add a projects/worktrees guide and document `takopi init` behavior in the readme

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

- add an opencode runner via the `opencode` cli with json event parsing and resume support [#22](https://github.com/banteg/takopi/pull/22)
- add a pi agent runner via the `pi` cli with jsonl streaming and resume support [#24](https://github.com/banteg/takopi/pull/24)
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

- add a claude code runner via the `claude` cli with stream-json parsing and resume support [#9](https://github.com/banteg/takopi/pull/9)
- auto-discover engine backends and generate cli subcommands from the registry [#12](https://github.com/banteg/takopi/pull/12)
- add `BaseRunner` session locking plus a `JsonlSubprocessRunner` helper for jsonl subprocess engines
- add jsonl stream parsing and subprocess helpers for runners
- lazily allocate per-session locks and streamline backend setup/install metadata
- improve startup message formatting and markdown rendering
- add a debug onboarding helper for setup troubleshooting

### breaking

- runner implementations must define explicit resume parsing/formatting (no implicit standard resume pattern)

### fixes

- stop leaking a hidden `engine-id` cli option on engine subcommands

### docs

- add a runner guide plus claude code docs (runner, events, stream-json cheatsheet)
- clarify the claude runner file layout and add guidance for jsonl-based runners
- document "minimal" runner mode: started+completed only, completed-only actions allowed

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
- terminate codex process groups on cancel (posix)
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
