# Adding a Runner

This guide explains how to add a **new engine runner** to Takopi.

A *runner* is the adapter between an engine-specific CLI (Codex, Claude Code, …) and Takopi’s
**normalized event model** (`StartedEvent`, `ActionEvent`, `CompletedEvent`).

If you are building an external plugin package, read `docs/plugins.md` first.

Takopi is designed so that adding a runner usually means **adding one new module** under
`src/takopi/runners/` plus a small **msgspec schema** module under `src/takopi/schemas/`—
no changes to the bridge, renderer, or CLI.

When writing code intended for plugins, prefer importing from `takopi.api`
instead of internal modules.

The walkthrough below uses an **imaginary engine** named **Acme** (`acme`) and intentionally mirrors
the patterns used in `runners/claude.py`.

---

## What “done” looks like

After you add a runner, you should be able to:

- Run `takopi acme` (CLI subcommand is auto-registered).
- Start a new session and get a resume line like `` `acme --resume <token>` ``.
- Reply to any bot message containing that resume line and continue the same session.
- See progress updates (optional) and always get a final completion event.

---

## Mental model

### 1) Takopi owns the domain model

Takopi’s core types live in `takopi.model`:

- `ResumeToken(engine, value)`
- `StartedEvent(engine, resume, title?, meta?)`
- `ActionEvent(engine, action, phase, ok?, message?, level?)`
- `CompletedEvent(engine, ok, answer, resume?, error?, usage?)`

Runners **must not** invent new event types. They translate engine output into these.

### 2) The runner contract (invariants)

A run must produce events with these invariants (see `tests/test_runner_contract.py`):

- Exactly **one** `StartedEvent`.
- Exactly **one** `CompletedEvent`.
- `CompletedEvent` is the **last** event.
- `CompletedEvent.resume == StartedEvent.resume` (same token).

Action events are optional (minimal runner mode):

- Minimum viable runner: `StartedEvent` → `CompletedEvent`.
- You may add `ActionEvent`s later (recommended for better progress UX).

### 3) Resume lines are runner-owned

Takopi deliberately treats the runner as the authority for:

- How a resume line looks in chat (`format_resume()`)
- How to parse a resume token out of text (`extract_resume()`)
- How to detect a resume line reliably (`is_resume_line()`)

This matters because Takopi’s Telegram truncation logic preserves resume lines.

---

## Step-by-step: add the imaginary `acme` runner

### Step 1 — Pick an engine id + resume command

Choose a stable engine id string. This string becomes:

- The config table name (`[acme]` in `takopi.toml`)
- The CLI subcommand (`takopi acme`)
- The `ResumeToken.engine`

Engine ids must match the plugin ID regex:

```
^[a-z0-9_]{1,32}$
```

For Acme we’ll use:

- Engine id: `"acme"`
- Canonical resume command embedded in chat: `` `acme --resume <token>` ``

#### Write a resume regex

Follow the pattern used by Claude/Codex: accept optional backticks, be case-insensitive,
match full line, and capture a group named `token`.

```py
_RESUME_RE = re.compile(
    r"(?im)^\s*`?acme\s+--resume\s+(?P<token>[^`\s]+)`?\s*$"
)
```

Why this shape?

- `(?m)` lets `^`/`$` match per-line inside multi-line messages.
- Optional backticks (`\`?`) lets you match Telegram inline-code formatting.
- Capturing the **last** token in a message lets users paste multiple resume lines.

---

### Step 2 — Create `src/takopi/schemas/acme.py` + `src/takopi/runners/acme.py`

Create a new schema module and a runner module:

```
src/takopi/schemas/
  codex.py
  acme.py    # ← new

src/takopi/runners/
  codex.py
  claude.py
  mock.py
  acme.py    # ← new
```

Takopi discovers engines via **entrypoints**. Every engine backend must be exposed
as an entrypoint under `takopi.engine_backends`, and the entrypoint name must match
the backend id.

For in-repo engines, add an entrypoint in `pyproject.toml`:

```toml
[project.entry-points."takopi.engine_backends"]
acme = "takopi.runners.acme:BACKEND"
```

For external plugins, use your package’s `pyproject.toml` with the same group.

---

### Step 3 — Translate Acme JSONL into Takopi events

Most CLIs we integrate are JSONL-streaming processes.

Takopi provides `JsonlSubprocessRunner`, which:

- spawns the CLI
- drains stderr and logs it
- reads stdout line-by-line as JSONL bytes
- calls your `decode_jsonl(...)` and then `translate(...)` to convert each event into Takopi events
- guarantees “exactly one CompletedEvent” behavior
- provides safe fallbacks for rc != 0 or stream ending without a completion event

#### Define a state object

Copy the Claude pattern: create a small dataclass to hold streaming state.

Common things to track:

- `factory`: `EventFactory` instance for creating Takopi events and tracking resume
- `pending_actions`: map tool_use_id → `Action` so tool results can complete them
- `last_assistant_text`: fallback for final answer if the engine omits it
- `note_seq`: counter used by `JsonlSubprocessRunner.note_event(...)`

```py
from dataclasses import dataclass, field

from ..events import EventFactory

@dataclass
class AcmeStreamState:
    factory: EventFactory = field(default_factory=lambda: EventFactory(ENGINE))
    pending_actions: dict[str, Action] = field(default_factory=dict)
    last_assistant_text: str | None = None
    note_seq: int = 0
```

#### Define a msgspec schema (recommended path)

Codex now decodes JSONL with **msgspec**, and new runners should follow that pattern.
Create a small schema module under `src/takopi/schemas/` and expose a `decode_event(...)`
function. Only include the event shapes your CLI actually emits.

Minimal example:

```py
from __future__ import annotations

from typing import Any, Literal, TypeAlias

import msgspec


class SessionStart(msgspec.Struct, tag="session.start", kw_only=True):
    session_id: str
    model: str | None = None


class ToolUse(msgspec.Struct, tag="tool.use", kw_only=True):
    id: str
    name: str
    input: dict[str, Any] | None = None


class ToolResult(msgspec.Struct, tag="tool.result", kw_only=True):
    tool_use_id: str
    content: Any
    is_error: bool | None = None


class Final(msgspec.Struct, tag="final", kw_only=True):
    session_id: str
    ok: bool
    answer: str | None = None
    error: str | None = None


AcmeEvent: TypeAlias = SessionStart | ToolUse | ToolResult | Final

_DECODER = msgspec.json.Decoder(AcmeEvent)


def decode_event(data: bytes | str) -> AcmeEvent:
    return _DECODER.decode(data)
```

#### Decide what Acme emits

For this guide, assume Acme outputs events like:

```json
{"type":"session.start","session_id":"acme_01","model":"acme-large"}
{"type":"tool.use","id":"toolu_1","name":"Bash","input":{"command":"ls"}}
{"type":"tool.result","tool_use_id":"toolu_1","content":"ok","is_error":false}
{"type":"final","session_id":"acme_01","ok":true,"answer":"Done."}
```

#### Map them to Takopi events

Use this mapping (mirrors Claude’s approach):

- `session.start` → `StartedEvent(engine="acme", resume=ResumeToken("acme", session_id))`
- `tool.use` → `ActionEvent(phase="started")` and stash action in `pending_actions`
- `tool.result` → `ActionEvent(phase="completed", ok=...)` and pop from `pending_actions`
- `final` → `CompletedEvent(ok, answer, resume)`

**Important:** emit exactly one `CompletedEvent`.

#### Make the translator a pure function

Claude keeps translation logic in a standalone function (`translate_claude_event(...)`).
This makes it easy to unit test without spawning a subprocess.

Do the same for Acme. Use pattern matching against msgspec shapes, and rely on the
`EventFactory` (as in Codex/Claude) to standardize event creation:

```py
def translate_acme_event(
    event: acme_schema.AcmeEvent,
    *,
    title: str,
    state: AcmeStreamState,
    factory: EventFactory,
) -> list[TakopiEvent]:
    match event:
        case acme_schema.SessionStart(session_id=session_id, model=model):
            if not session_id:
                return []
            event_title = str(model) if model else title
            token = ResumeToken(engine=ENGINE, value=session_id)
            return [factory.started(token, title=event_title)]

        case acme_schema.ToolUse(id=tool_id, name=name, input=tool_input):
            if not tool_id:
                return []
            tool_input = tool_input or {}
            name = str(name or "tool")

            # Keep titles short and friendly.
            # (Claude uses takopi.utils.paths.relativize_command / relativize_path)
            kind: ActionKind = "tool"
            title = name
            if name in {"Bash", "Shell"}:
                kind = "command"
                title = relativize_command(str(tool_input.get("command") or name))

            action = Action(
                id=tool_id,
                kind=kind,
                title=title,
                detail={"name": name, "input": tool_input},
            )
            state.pending_actions[action.id] = action
            return [
                factory.action_started(
                    action_id=action.id,
                    kind=action.kind,
                    title=action.title,
                    detail=action.detail,
                )
            ]

        case acme_schema.ToolResult(
            tool_use_id=tool_use_id, content=content, is_error=is_error
        ):
            if not tool_use_id:
                return []
            action = state.pending_actions.pop(tool_use_id, None)
            if action is None:
                action = Action(
                    id=tool_use_id,
                    kind="tool",
                    title="tool result",
                    detail={},
                )

            result_text = (
                ""
                if content is None
                else (content if isinstance(content, str) else str(content))
            )
            detail = dict(action.detail)
            detail.update(
                {"result_preview": result_text, "is_error": bool(is_error)}
            )

            return [
                factory.action_completed(
                    action_id=action.id,
                    kind=action.kind,
                    title=action.title,
                    ok=not bool(is_error),
                    detail=detail,
                )
            ]

        case acme_schema.Final(session_id=session_id, ok=ok, answer=answer, error=error):
            answer = answer or ""
            if ok and not answer and state.last_assistant_text:
                answer = state.last_assistant_text

            resume = (
                ResumeToken(engine=ENGINE, value=session_id) if session_id else None
            )

            if ok:
                return [factory.completed_ok(answer=answer, resume=resume)]

            error_text = str(error) if error else "acme run failed"
            return [
                factory.completed_error(
                    error=error_text,
                    answer=answer,
                    resume=resume,
                )
            ]

        case _:
            return []
```

This is intentionally close to Claude’s structure:

- Match on the msgspec event type
- Handle “init/session start” first
- Emit action-start and action-complete events
- Emit a final `CompletedEvent`

---

### Step 4 — Implement the `AcmeRunner` class

Most engines can implement a runner by combining:

- `ResumeTokenMixin` (resume parsing + resume-line detection)
- `JsonlSubprocessRunner` (process + JSONL streaming + completion semantics)

#### Why this combo?

It matches Claude/Codex:

- Runner owns resume format/regex.
- Base class owns locking and subprocess lifecycle.
- Translation stays in a pure function and is easily testable.

#### Minimal skeleton

```py
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..backends import EngineBackend, EngineConfig
from ..model import (
    EngineId,
    ResumeToken,
    TakopiEvent,
)

from ..runner import JsonlSubprocessRunner, ResumeTokenMixin, Runner
from ..schemas import acme as acme_schema

logger = logging.getLogger(__name__)

ENGINE: EngineId = "acme"
_RESUME_RE = re.compile(
    r"(?im)^\s*`?acme\s+--resume\s+(?P<token>[^`\s]+)`?\s*$"
)


@dataclass
class AcmeRunner(ResumeTokenMixin, JsonlSubprocessRunner):
    engine: EngineId = ENGINE
    resume_re: re.Pattern[str] = _RESUME_RE

    acme_cmd: str = "acme"
    model: str | None = None
    allowed_tools: list[str] | None = None
    session_title: str = "acme"
    logger = logger

    def format_resume(self, token: ResumeToken) -> str:
        # Override because our canonical resume command is "acme --resume ...".
        if token.engine != ENGINE:
            raise RuntimeError(f"resume token is for engine {token.engine!r}")
        return f"`acme --resume {token.value}`"

    def command(self) -> str:
        return self.acme_cmd

    def build_args(
        self,
        prompt: str,
        resume: ResumeToken | None,
        *,
        state: Any,
    ) -> list[str]:
        _ = prompt, state
        args = ["--output-format", "stream-json", "--verbose"]
        if resume is not None:
            args.extend(["--resume", resume.value])
        if self.model is not None:
            args.extend(["--model", str(self.model)])
        if self.allowed_tools:
            args.extend(["--allowed-tools", ",".join(self.allowed_tools)])
        return args

    def stdin_payload(
        self,
        prompt: str,
        resume: ResumeToken | None,
        *,
        state: Any,
    ) -> bytes | None:
        _ = resume, state
        # Acme reads the prompt from stdin.
        return prompt.encode()

    def new_state(self, prompt: str, resume: ResumeToken | None) -> AcmeStreamState:
        _ = prompt, resume
        return AcmeStreamState()

    def decode_jsonl(
        self,
        *,
        raw: bytes,
        line: bytes,
        state: AcmeStreamState,
    ) -> acme_schema.AcmeEvent | None:
        _ = raw, state
        return acme_schema.decode_event(line)

    def translate(
        self,
        data: acme_schema.AcmeEvent,
        *,
        state: AcmeStreamState,
        resume: ResumeToken | None,
        found_session: ResumeToken | None,
    ) -> list[TakopiEvent]:
        _ = resume, found_session
        return translate_acme_event(
            data,
            title=self.session_title,
            state=state,
            factory=state.factory,
        )
```

Notes:

- `JsonlSubprocessRunner` already enforces the “exactly one completed event” rule.
- When `resume=None`, Takopi will acquire a per-session lock after it sees the first
  `StartedEvent`. This is why emitting `StartedEvent` early is important.

#### Optional but recommended overrides (Claude-inspired)

Depending on how robust you want the integration, consider adding:

- `env(...)`: to strip or inject environment variables (Claude strips `ANTHROPIC_API_KEY`
  unless configured to use API billing).
- `invalid_json_events(...)`: emit a helpful warning `ActionEvent` on malformed JSONL.
- `decode_error_events(...)`: log + drop `msgspec.DecodeError` if the engine emits garbage.
- `process_error_events(...)`: customize rc != 0 behavior.
- `stream_end_events(...)`: handle “process exited cleanly but never emitted a final event”.

Claude uses these to produce better failures instead of silent hangs.

---

### Step 5 — Add `build_runner(...)` and `BACKEND`

Takopi needs a way to build your runner from config.

Follow the pattern in `runners/claude.py`:

```py
def build_runner(config: EngineConfig, _config_path: Path) -> Runner:
    acme_cmd = "acme"

    model = config.get("model")
    allowed_tools = config.get("allowed_tools")

    title = str(model) if model is not None else "acme"

    return AcmeRunner(
        acme_cmd=acme_cmd,
        model=model,
        allowed_tools=allowed_tools,
        session_title=title,
    )


BACKEND = EngineBackend(
    id="acme",
    build_runner=build_runner,
    install_cmd="npm install -g @acme/acme-cli",
)
```

That’s it for wiring.

Because engine backends are auto-discovered (`takopi.engines`), you do **not** need
to register the runner elsewhere.

If the binary name differs from the engine id, set:

- `EngineBackend(cli_cmd="acme-cli")`

so onboarding can find it on PATH.

---

### Step 6 — Add tests (copy Claude’s testing strategy)

A good runner PR usually contains 3 types of tests.

#### 1) Resume parsing tests

Copy `tests/test_claude_runner.py::test_claude_resume_format_and_extract`.

For Acme, assert:

- `format_resume(...)` outputs the canonical resume line.
- `extract_resume(...)` can parse it back out.
- It ignores other engines’ resume lines.

#### 2) Translation unit tests (fixtures)

Claude’s translation tests load JSONL fixtures and feed them into the pure translator.

Do the same:

- `tests/fixtures/acme_stream_success.jsonl`
- `tests/fixtures/acme_stream_error.jsonl`

Then assert:

- first event is `StartedEvent`
- action events are correct (ids, kinds, titles)
- the last event is a `CompletedEvent`
- completed.resume matches started.resume

If you use msgspec, also add a tiny schema sanity test (pattern from
`tests/test_codex_schema.py`) that decodes your fixture with
`takopi.schemas.<engine>.decode_event`.

#### 3) Lock/serialization tests (optional, but great)

Claude has async tests proving that:

- two runs with the same resume token serialize (`max_in_flight == 1`)
- a new session run locks correctly after it emits `StartedEvent`

If your runner uses `JsonlSubprocessRunner`, you get most of this for free, but having
one targeted test catches regressions.

---

## Common pitfalls (and how Claude avoided them)

- **StartedEvent arrives too late**
  - If you wait until the end to emit `StartedEvent`, Takopi can’t acquire the per-session lock
    early and another task might resume the same session concurrently.
  - Emit `StartedEvent` immediately when you learn the session id.

- **Multiple completion events**
  - Some CLIs emit multiple “final-ish” events. Decide which one becomes Takopi’s `CompletedEvent`.
  - `JsonlSubprocessRunner` will stop reading after the first `CompletedEvent` it sees.

- **Missing completion event**
  - Claude handles “stream ended without a result event” by emitting a synthetic `CompletedEvent`
    in `stream_end_events(...)`.

- **Unhelpful error reporting**
  - Include stderr tail in a warning action (Claude includes `stderr_tail` in `detail`).

- **Resume line gets truncated**
  - Ensure `is_resume_line()` matches your `format_resume()` output. Takopi tries to preserve
    resume lines during truncation.

- **Leaking secrets**
  - If your engine can run in “subscription mode” without env keys, strip env vars like Claude
    does with `ANTHROPIC_API_KEY`.

---

## Final checklist

Before you call the runner “done”:

- [ ] `takopi acme` appears automatically (module exports `BACKEND`).
- [ ] `format_resume()` matches `extract_resume()` + `is_resume_line()`.
- [ ] Translation emits exactly one `StartedEvent` and one `CompletedEvent`.
- [ ] `CompletedEvent.resume` matches `StartedEvent.resume`.
- [ ] rc != 0 produces a failure `CompletedEvent` (via `process_error_events`).
- [ ] “no final event” produces a failure `CompletedEvent` (via `stream_end_events`).
- [ ] Tests cover resume parsing + at least one translation fixture.
