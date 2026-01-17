# Takopi Architecture & Lifecycle

## Layer Diagram

```mermaid
flowchart TB
    subgraph CLI["CLI Layer"]
        cli[cli.py]
        cli_desc["Entry point, config loading, lock file"]
    end

    subgraph Plugins["Plugin Layer"]
        entrypoints[plugins.py<br/>entrypoint discovery]
        engines[engines.py]
        transports[transports.py]
        commands[commands.py]
        api[api.py<br/>public plugin API]
    end

    subgraph Orchestration["Orchestration Layer"]
        router[AutoRouter<br/>router.py]
        scheduler[ThreadScheduler<br/>scheduler.py]
        projects[ProjectsConfig<br/>config.py]
        runtime[TransportRuntime<br/>transport_runtime.py]
    end

    subgraph Bridge["Bridge Layer"]
        tg_bridge[telegram/bridge.py<br/>run_main_loop]
        runner_bridge[runner_bridge.py<br/>handle_message]
    end

    subgraph Runner["Runner Layer"]
        runner_proto[Runner Protocol<br/>runner.py]
        runners[runners/<br/>claude, codex, opencode, pi]
        schemas[schemas/<br/>JSONL decoders]
    end

    subgraph Transport["Transport Layer"]
        transport[Transport Protocol]
        presenter[Presenter Protocol]
        tg_client[telegram/client.py]
        tg_render[telegram/render.py]
        markdown[markdown.py]
    end

    subgraph External["External"]
        agent_clis[Agent CLIs<br/>claude, codex, pi]
        telegram_api[Telegram Bot API]
    end

    cli --> router
    cli --> scheduler
    cli --> projects
    cli --> engines
    cli --> transports
    cli --> commands
    engines --> entrypoints
    transports --> entrypoints
    commands --> entrypoints
    router --> runtime
    projects --> runtime
    router --> tg_bridge
    scheduler --> tg_bridge
    runtime --> tg_bridge
    tg_bridge --> commands
    tg_bridge --> runner_bridge
    runner_bridge --> runner_proto
    runner_proto --> runners
    runners --> schemas
    runners --> agent_clis
    runner_bridge --> transport
    runner_bridge --> presenter
    transport --> tg_client
    presenter --> tg_render
    presenter --> markdown
    tg_client --> telegram_api
```

---

## Plugin Architecture

Takopi discovers plugins via Python entrypoints and keeps loading lazy:

- **Engine backends** (`takopi.engine_backends`)
- **Transport backends** (`takopi.transport_backends`)
- **Command backends** (`takopi.command_backends`)

Entrypoint names become plugin IDs, are validated up front (reserved names, regex),
and are only loaded when needed. The public surface for plugin authors lives in
`takopi.api`, while transports and commands interact with core routing via
`TransportRuntime`.

---

## Domain Model

```mermaid
classDiagram
    class ResumeToken {
        +engine: EngineId
        +value: str
    }

    class Action {
        +id: str
        +kind: ActionKind
        +title: str
        +detail: dict
    }

    class StartedEvent {
        +type: "started"
        +engine: EngineId
        +resume: ResumeToken
        +title: str?
    }

    class ActionEvent {
        +type: "action"
        +engine: EngineId
        +action: Action
        +phase: started|updated|completed
        +ok: bool?
        +message: str?
    }

    class CompletedEvent {
        +type: "completed"
        +engine: EngineId
        +ok: bool
        +answer: str
        +resume: ResumeToken?
        +usage: dict?
    }

    StartedEvent --> ResumeToken
    ActionEvent --> Action
    CompletedEvent --> ResumeToken

    note for Action "ActionKind: command | tool | file_change |\nweb_search | subagent | note | turn | warning | telemetry"
```

---

## Message Lifecycle

```mermaid
sequenceDiagram
    participant User
    participant Telegram
    participant Bridge as telegram/bridge.py
    participant Scheduler as ThreadScheduler
    participant RunnerBridge as runner_bridge.py
    participant Runner
    participant AgentCLI as Agent CLI
    participant Command as Command Plugin

    User->>Telegram: Send message
    Telegram->>Bridge: poll_incoming()

    Bridge->>Bridge: Parse slash command
    alt Command plugin
        Bridge->>Command: handle(ctx)
        Command->>RunnerBridge: run_one/run_many (optional)
        RunnerBridge->>Telegram: Send progress/final
    else Default routing
        Bridge->>Bridge: Parse directives<br/>(/&lt;engine-id&gt;, /&lt;project-alias&gt;, @branch)
        Bridge->>Bridge: Extract resume token<br/>from reply
        Bridge->>Bridge: Resolve worktree<br/>(if @branch)

        Bridge->>Scheduler: enqueue(ThreadJob)
        Scheduler->>RunnerBridge: handle_message()

        RunnerBridge->>Telegram: Send progress message
        RunnerBridge->>Runner: run(prompt, resume)
    end

    Runner->>AgentCLI: Spawn subprocess

    loop JSONL Stream
        AgentCLI-->>Runner: JSONL event
        Runner-->>RunnerBridge: TakopiEvent
        RunnerBridge->>Telegram: Edit progress message
    end

    AgentCLI-->>Runner: Completed
    Runner-->>RunnerBridge: CompletedEvent
    RunnerBridge->>Telegram: Send final answer
    RunnerBridge->>Telegram: Delete progress message
```

---

## Runner Execution Flow

```mermaid
flowchart TD
    A[runner.run\nprompt, resume_token] --> B[Acquire Session Lock<br/>SessionLockMixin]

    B --> C[Build Command]

    C --> D{Engine?}
    D -->|Claude| D1["claude --print --output-format stream-json<br/>[--resume id] prompt"]
    D -->|Codex| D2["codex exec --json<br/>[resume &lt;token&gt;] -"]
    D -->|Pi| D3["pi --print --mode json<br/>--session &lt;id&gt; &lt;prompt&gt;"]
    D -->|OpenCode| D4["opencode run --format json<br/>[--session id] -- &lt;prompt&gt;"]

    D1 --> E[Spawn Subprocess<br/>anyio.open_process]
    D2 --> E
    D3 --> E
    D4 --> E

    E --> F[Stream JSONL from stdout]

    F --> G[Decode with msgspec]
    G --> H[Translate to TakopiEvent]
    H --> I[yield event]
    I --> F

    F -->|EOF| J[Return]
```

---

## Resume Token Flow

```mermaid
sequenceDiagram
    participant User
    participant Bridge
    participant Runner
    participant CLI as Agent CLI

    Note over User,CLI: New Conversation
    User->>Bridge: "fix the bug"
    Bridge->>Runner: run(prompt, None)
    Runner->>CLI: claude "fix the bug"
    CLI-->>Runner: StartedEvent(resume=abc123)
    Runner-->>Bridge: Stream events
    Bridge->>User: Final message with:<br/>claude --resume abc123<br/>ctx: project @branch

    Note over User,CLI: Resume Conversation
    User->>Bridge: Reply: "now add tests"
    Bridge->>Bridge: extract_resume(reply_text)<br/>→ ResumeToken(claude, abc123)
    Bridge->>Bridge: parse_ctx_line()<br/>→ project, branch
    Bridge->>Runner: run("now add tests", token)
    Runner->>CLI: claude --resume abc123 "now add tests"
    CLI-->>Runner: Continues session
    Runner-->>Bridge: Stream events
    Bridge->>User: Final message
```

---

## Component Dependencies

```mermaid
flowchart TD
    cli[cli.py] --> config[config.py]
    cli --> engines[engines.py]
    cli --> transports[transports.py]
    cli --> commands[commands.py]
    cli --> lockfile[lockfile.py]

    engines --> plugins[plugins.py]
    transports --> plugins
    commands --> plugins

    engines --> backends[backends.py]

    backends --> runners[runners/]
    backends --> runner[runner.py]

    subgraph runners[runners/]
        claude[claude.py]
        codex[codex.py]
        opencode[opencode.py]
        pi[pi.py]
    end

    subgraph schemas[schemas/]
        claude_s[claude.py]
        codex_s[codex.py]
        opencode_s[opencode.py]
        pi_s[pi.py]
    end

    claude --> claude_s
    codex --> codex_s
    opencode --> opencode_s
    pi --> pi_s

    cli --> router[router.py]
    tg_bridge --> runtime[transport_runtime.py]
    runtime --> router
    runtime --> config
    tg_bridge --> commands

    runner --> runner_bridge[runner_bridge.py]
    runner_bridge --> tg_bridge

    tg_bridge --> client[telegram/client.py]
    tg_bridge --> render[telegram/render.py]

    client --> transport[transport.py]

    runner_bridge --> progress[progress.py]
    runner_bridge --> events[events.py]

    render --> presenter[presenter.py]
    presenter --> markdown[markdown.py]
```

---

## Configuration Structure

```mermaid
flowchart LR
    subgraph Config["~/.takopi/"]
        toml[takopi.toml]
        lock[takopi.lock]
    end

    subgraph toml_contents["takopi.toml"]
        direction TB
        global["transport<br/>default_engine<br/>default_project"]
        telegram_cfg["[transports.telegram]<br/>bot_token = ...<br/>chat_id = ..."]
        plugins_cfg["[plugins]<br/>enabled = [...]"]
        plugins_extra["[plugins.mycommand]<br/>setting = ..."]
        claude_cfg["[claude]<br/>model = ..."]
        codex_cfg["[codex]<br/>model = ..."]
        projects_cfg["[projects.alias]<br/>path = ...<br/>worktrees_dir = ...<br/>default_engine = ..."]
    end

    toml --> toml_contents
```

---

## Thread Scheduling

```mermaid
flowchart TD
    subgraph Incoming[Incoming Messages]
        m1[Message 1<br/>new thread]
        m2[Message 2<br/>reply to thread A]
        m3[Message 3<br/>reply to thread A]
        m4[Message 4<br/>new thread]
    end

    subgraph Scheduler[ThreadScheduler]
        direction TB
        q1[Thread A Queue]
        q2[Thread B Queue]
        q3[Thread C Queue]
    end

    subgraph Workers[Worker Tasks]
        w1[Worker A]
        w2[Worker B]
        w3[Worker C]
    end

    m1 --> q2
    m2 --> q1
    m3 --> q1
    m4 --> q3

    q1 --> w1
    q2 --> w2
    q3 --> w3

    w1 --> runner1[Runner.run]
    w2 --> runner2[Runner.run]
    w3 --> runner3[Runner.run]

    note1[Jobs in same thread<br/>execute sequentially]
    note2[Different threads<br/>execute in parallel]
```

---

## Summary

| Layer | Components | Responsibility |
|-------|------------|----------------|
| **CLI** | `cli.py` | Entry point, config, lock |
| **Plugins** | `plugins.py`, `engines.py`, `transports.py`, `commands.py`, `api.py` | Entrypoint discovery, plugin loading, public API boundary |
| **Orchestration** | `router.py`, `scheduler.py`, `config.py` | Engine selection, job queuing, project config |
| **Bridge** | `telegram/bridge.py`, `runner_bridge.py` | Message handling, execution coordination |
| **Runner** | `runner.py`, `runners/*.py`, `schemas/*.py` | Agent CLI subprocess, JSONL parsing, event translation |
| **Transport** | `transport.py`, `presenter.py`, `telegram/client.py` | Telegram API, message rendering |
| **Domain** | `model.py`, `progress.py`, `events.py` | Event types, action tracking |
| **Utils** | `worktrees.py`, `utils/*.py`, `markdown.py` | Git worktrees, formatting, paths |
