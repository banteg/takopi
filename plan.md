## Takopi implementer guide: add **Pi (pi-mono)** as the next runner

This guide is written for **Takopi v0.4.0** (the codebase you shared) and targets the **Pi coding agent CLI** from the **pi-mono** monorepo as a first‑class Takopi engine.

The outcome is a new Takopi engine:

* Engine id: **`pi`**
* Runner module: `src/takopi/runners/pi.py`
* Invokes the `pi`/Pi coding-agent CLI in **headless JSONL streaming mode** (one JSON object per line).
* Supports thread continuation via **resume tokens** (Takopi’s canonical “resume line” mechanism; see `docs/specification.md`).

---

# 1) What you’re integrating

The **pi-mono** repo is a monorepo containing multiple packages, including:

* `@mariozechner/pi-coding-agent` (interactive coding agent CLI)
* `@mariozechner/pi-ai` (unified model/provider API)
* `@mariozechner/pi-agent-core` (agent runtime), etc.

For Takopi, you integrate **the CLI** as a subprocess runner, and normalize its event stream into Takopi’s event model (`started`, `action`, `completed`). The coding agent can stream “all agent events as JSON (one event per line)”, which is exactly what Takopi’s `JsonlSubprocessRunner` expects.

---

# 2) Prerequisites and environment expectations

## 2.1 Install the Pi CLI

Pi skills docs explicitly call out installing the coding agent globally via npm.

Practical options you can document in Takopi onboarding:

* `npm install -g @mariozechner/pi-coding-agent`
* (If your setup uses the `@mariozechner/pi` wrapper package / `pi` binary, use that instead; you’ll validate the actual command name in §3.)

### Decide your canonical executable name

Takopi’s onboarding checks a single command on PATH (`backend.cli_cmd or backend.id`). Your runner should choose one of:

* `pi` (preferred if that’s the actual binary you’ll run), or
* `pi-coding-agent` (if that’s the installed binary on your system).

You can keep the **engine id** as `pi` regardless, and set `cli_cmd="pi"` if needed.

## 2.2 Ensure Pi has auth configured

Pi credential storage recently moved to `~/.pi/agent/auth.json` (with migration on first run). That’s useful to call out because many “first run” failures are “not authenticated / missing key”.

In other words, implementers should run the Pi CLI once interactively to set up credentials before using it through Takopi.

## 2.3 Know where sessions live (for debugging)

Pi sessions are stored under the `~/.pi/…` hierarchy (issues mention `.pi` as the location for session logs).

This matters because:

* It’s your best source of truth when you’re validating resume behavior.
* It helps diagnose “Takopi didn’t capture the session id” problems.

---

# 3) Confirm the Pi CLI contract you will rely on

Before writing Takopi code, you want **two concrete facts**:

1. **How to run Pi headless** (non-TUI) for a single prompt
2. **How to stream JSONL events** to stdout (one JSON per line)

The Pi docs state there is a JSON output mode that streams agent events as JSON lines.
The exact flag spelling may evolve, so build your runner around a small “CLI contract” you validate locally:

### 3.1 Validation checklist

Run these manually in a scratch repo:

* `pi --help`
  Confirm:

  * how to pass a prompt (argument vs stdin)
  * how to enable JSON event streaming
  * how to set model/provider (if supported)
  * how to resume a session (flag name and token format)

* `pi <headless args> <prompt>`
  Confirm:

  * stdout is **newline-delimited JSON objects** only (no banners)
  * stderr contains logs/debug only (Takopi drains stderr separately)

* `pi <headless args> --resume <token> <prompt>`
  Confirm:

  * it continues an existing thread/session and emits the same session identifier early

### 3.2 Decide on prompt transport

Takopi supports both patterns:

* **Prompt via stdin**: easiest when the CLI supports a `--prompt-stdin` style flag
* **Prompt as positional arg**: like Claude runner uses `--` and passes prompt as last arg

Pick one and implement it deterministically. If Pi supports both, prefer stdin because it avoids quoting and argument length issues.

---

# 4) Runner design in Takopi

Takopi’s runner architecture (v0.4.0) expects:

* `Runner.run(prompt, resume)` yields **Takopi events** (started/action/completed)
* For resumable engines, the runner must:

  * format canonical resume line (`format_resume`)
  * extract resume tokens from user/reply text (`extract_resume`)
  * detect single-line resume commands (`is_resume_line`)
* Concurrency: runs are serialized per resume token (handled by `BaseRunner`/`SessionLockMixin` if you emit `StartedEvent` correctly). See `docs/specification.md`.

Given Pi emits JSONL events, your implementation should extend:

* `JsonlSubprocessRunner` for streaming parsing + subprocess lifecycle
* `ResumeTokenMixin` for resume codec

This mirrors `CodexRunner` and `ClaudeRunner`.

---

# 5) Implementation steps (code changes)

## 5.1 Add `src/takopi/runners/pi.py`

Create a new module exporting:

* `ENGINE: EngineId = EngineId("pi")`
* `_RESUME_RE` to match Pi’s canonical resume command
* `PiRunner(JsonlSubprocessRunner, ResumeTokenMixin)`
* `build_runner(config, config_path) -> Runner`
* `BACKEND = EngineBackend(id="pi", build_runner=..., install_cmd=..., cli_cmd=...)`

### 5.1.1 Choose a canonical resume line format

Takopi’s spec says the canonical ResumeLine is “the engine CLI resume command”. For Pi, make it:

```text
`pi --resume <token>`
```

…but only if that matches Pi’s actual CLI. If Pi uses `pi resume <token>`, then make that the canonical line instead.

Your regex should:

* accept backticks or plain
* accept long/short flags if the CLI supports them
* treat the token as opaque (don’t validate UUID format)

Example regex pattern (adapt to Pi’s real CLI):

```py
_RESUME_RE = re.compile(r"(?im)^\s*`?pi\s+(?:--resume|-r)\s+(?P<token>[^`\s]+)`?\s*$")
```

## 5.2 Define config schema for `[pi]`

Takopi config is TOML (`.takopi/takopi.toml` or `~/.takopi/takopi.toml`). Your runner should accept a minimal, flexible schema so you don’t have to chase Pi CLI changes.

Recommended:

```toml
default_engine = "pi"

[pi]
cmd = "pi"                 # optional; defaults to "pi"
extra_args = []            # optional list of strings, appended verbatim
model = "…"                # optional
provider = "…"             # optional
json_mode = true           # optional; default true
session_title = "pi"       # optional; defaults to model or "pi"
```

Notes:

* `extra_args` is crucial for forwarding new Pi flags without changing Takopi.
* `cmd` lets users pin to a full path or alternate executable name.

## 5.3 Implement the subprocess invocation

Your `PiRunner` must implement:

* `command()` → configured `cmd`
* `build_args(prompt, resume, state=...)` → list[str]
* optionally `stdin_payload()` → bytes | None
* optionally `env()` → dict[str, str] | None

### 5.3.1 Hard requirements for Takopi

To work well with `JsonlSubprocessRunner`, Pi must output:

* **only JSON lines on stdout** (Takopi treats non-JSON as warnings or ignores them)
* `StartedEvent` must be emitted once you learn the session token
* a `CompletedEvent` must be emitted when the run ends (success or failure)

### 5.3.2 Suggested subprocess defaults

Even if Pi’s JSON mode is clean, add these to reduce noise:

* Environment:

  * `NO_COLOR=1` (prevents ANSI in tool outputs; safer parsing)
  * optionally `CI=1` (some CLIs disable TUI in CI)

If Pi supports explicit flags for:

* headless / non-interactive
* disable TUI
* log level / quiet mode

…use them.

---

# 6) Translate Pi JSONL events into Takopi events

This is the heart of the runner.

## 6.1 Create a stream state

Like `ClaudeStreamState`, you’ll want to keep minimal state across events:

```py
@dataclass(slots=True)
class PiStreamState:
    pending_actions: dict[str, Action] = field(default_factory=dict)
    last_assistant_text: str | None = None
    note_seq: int = 0
```

* `pending_actions`: track tool calls across start/result events
* `last_assistant_text`: fall back answer if final result doesn’t carry text
* `note_seq`: for warning events (you can reuse `JsonlSubprocessRunner.note_event()`)

## 6.2 Identify the “session start” event

Pi’s JSON stream includes “agent events” (message updates, tool executions, etc.).
You need to locate the event that carries the session id.

Implementation strategy:

* Check `event["type"]`
* When you see the init/start event, extract a stable token from:

  * `event.get("session_id")`, `event.get("sessionId")`, `event.get("session")`, or similar
* Emit:

```py
StartedEvent(engine=ENGINE, resume=ResumeToken(engine=ENGINE, value=session_id), title=stateful_title, meta={...})
```

Meta is optional but helpful (cwd, model, provider, tool set, etc.).

## 6.3 Tool calls → `ActionEvent`

Takopi requires stable action IDs within a run. The best source is:

* Pi tool call id (if present)
* otherwise a deterministic derived id (avoid random UUIDs)

Suggested mapping:

| Pi event kind        | Takopi action.kind | Title strategy      |
| -------------------- | ------------------ | ------------------- |
| shell/bash execution | `command`          | relativized command |
| file write/edit      | `file_change`      | relativized path    |
| web search/fetch     | `web_search`       | query/url           |
| other tools          | `tool`             | tool name           |

Use Takopi helpers:

* `relativize_command()` for commands
* `relativize_path()` for paths

### Handling “started/updated/completed”

Takopi supports minimal mode (you can emit only completed actions), but you’ll get better UX by emitting:

* `phase="started"` on tool call start
* `phase="completed"` on tool result

Store the action in `state.pending_actions[action_id]` on start, pop it on completion.

## 6.4 Assistant text → determine final answer

Takopi only needs the final answer, not token streaming.

Strategy:

* When you see an “assistant message finalization” event, update `state.last_assistant_text`
* When you see the “session end / result” event:

  * pick `answer = event.get("result") or event.get("answer") or state.last_assistant_text or ""`
  * determine `ok`:

    * `ok = event.get("ok") is True` or `event.get("is_error") is False`, etc.
  * if there is an error message, include it in `CompletedEvent.error`

## 6.5 Deal with noisy/unexpected events (e.g., compaction)

Pi session logs include events like `"type":"compaction"` (seen in the wild).
Your translator should safely ignore such events unless they carry something user-visible.

Rule of thumb:

* If you don’t recognize an event type, ignore it.
* If it has an obvious error payload, emit a warning `ActionEvent` with `kind="warning"` and keep going.

---

# 7) Concrete code skeleton (Takopi-side)

Below is an intentionally “fill in the blanks” skeleton that matches Takopi v0.4.0 patterns and keeps Pi-specific details isolated to a handful of functions.

```py
# src/takopi/runners/pi.py
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..backends import EngineBackend, EngineConfig
from ..config import ConfigError
from ..model import Action, ActionEvent, CompletedEvent, EngineId, ResumeToken, StartedEvent, TakopiEvent
from ..runner import JsonlSubprocessRunner, ResumeTokenMixin, Runner
from ..utils.paths import relativize_command, relativize_path

logger = logging.getLogger(__name__)

ENGINE: EngineId = EngineId("pi")

# TODO: match Pi’s actual resume syntax
_RESUME_RE = re.compile(r"(?im)^\s*`?pi\s+(?:--resume|-r)\s+(?P<token>[^`\s]+)`?\s*$")

@dataclass(slots=True)
class PiStreamState:
    pending_actions: dict[str, Action] = field(default_factory=dict)
    last_assistant_text: str | None = None
    note_seq: int = 0


def _action_event(*, action: Action, phase: str, ok: bool | None = None, message: str | None = None, level: str | None = None) -> ActionEvent:
    return ActionEvent(engine=ENGINE, action=action, phase=phase, ok=ok, message=message, level=level)


def translate_pi_event(event: dict[str, Any], *, state: PiStreamState, title: str) -> list[TakopiEvent]:
    etype = event.get("type")
    out: list[TakopiEvent] = []

    # 1) START
    if etype in {"session.start", "session.started", "run.start", "init"}:
        session_id = event.get("session_id") or event.get("sessionId") or event.get("session")
        if isinstance(session_id, str) and session_id:
            out.append(
                StartedEvent(
                    engine=ENGINE,
                    resume=ResumeToken(engine=ENGINE, value=session_id),
                    title=event.get("model") or title,
                    meta={k: event.get(k) for k in ("cwd", "model", "provider", "tools") if k in event},
                )
            )
        return out

    # 2) TOOL START
    if etype in {"tool.start", "tool.call"}:
        tool_id = event.get("id") or event.get("tool_call_id") or event.get("toolCallId")
        name = event.get("name") or event.get("tool")
        args = event.get("args") or event.get("input") or {}
        if isinstance(tool_id, str) and tool_id:
            kind = "tool"
            title_str = str(name or "tool")
            if str(name).lower() in {"bash", "shell", "cmd"} and isinstance(args, dict):
                cmd = args.get("command") or args.get("cmd")
                if cmd:
                    kind = "command"
                    title_str = relativize_command(str(cmd))
            if str(name).lower() in {"write", "edit"} and isinstance(args, dict):
                path = args.get("path") or args.get("file_path")
                if path:
                    kind = "file_change"
                    title_str = relativize_path(str(path))

            action = Action(id=str(tool_id), kind=kind, title=title_str, detail={"name": name, "input": args})
            state.pending_actions[action.id] = action
            out.append(_action_event(action=action, phase="started"))
        return out

    # 3) TOOL RESULT
    if etype in {"tool.result", "tool.end"}:
        tool_id = event.get("id") or event.get("tool_call_id") or event.get("toolCallId")
        if isinstance(tool_id, str) and tool_id and tool_id in state.pending_actions:
            action = state.pending_actions.pop(tool_id)
            is_error = event.get("is_error") is True or event.get("ok") is False
            detail = dict(action.detail)
            detail["result"] = event.get("result") or event.get("output")
            out.append(
                _action_event(
                    action=Action(id=action.id, kind=action.kind, title=action.title, detail=detail),
                    phase="completed",
                    ok=not is_error,
                )
            )
        return out

    # 4) ASSISTANT MESSAGE
    if etype in {"assistant.message", "message"}:
        role = event.get("role")
        if role == "assistant":
            text = event.get("text") or event.get("content")
            if isinstance(text, str) and text.strip():
                state.last_assistant_text = text
        return out

    # 5) END / RESULT
    if etype in {"session.end", "run.end", "result"}:
        session_id = event.get("session_id") or event.get("sessionId") or event.get("session")
        resume = ResumeToken(engine=ENGINE, value=session_id) if isinstance(session_id, str) and session_id else None
        answer = event.get("result") or event.get("answer") or state.last_assistant_text or ""
        ok = True
        if event.get("is_error") is True:
            ok = False
        if event.get("ok") is False:
            ok = False
        error = event.get("error") if not ok else None

        usage = event.get("usage") if isinstance(event.get("usage"), dict) else None

        out.append(
            CompletedEvent(engine=ENGINE, ok=ok, answer=str(answer or ""), resume=resume, error=error, usage=usage)
        )
        return out

    return out


class PiRunner(JsonlSubprocessRunner, ResumeTokenMixin):
    engine: EngineId = ENGINE
    resume_re = _RESUME_RE

    def __init__(self, *, pi_cmd: str, extra_args: list[str], model: str | None, provider: str | None, session_title: str):
        self.pi_cmd = pi_cmd
        self.extra_args = extra_args
        self.model = model
        self.provider = provider
        self.session_title = session_title

    def command(self) -> str:
        return self.pi_cmd

    def new_state(self, prompt: str, resume: ResumeToken | None) -> PiStreamState:
        return PiStreamState()

    def build_args(self, prompt: str, resume: ResumeToken | None, *, state: PiStreamState) -> list[str]:
        args: list[str] = []
        args.extend(self.extra_args)

        # TODO: put real JSON streaming / headless flags here once confirmed via `pi --help`
        # Example placeholders:
        # args += ["--format", "json"]
        # args += ["--prompt-stdin"]

        if self.model:
            # TODO: confirm flag name
            args += ["--model", self.model]
        if self.provider:
            # TODO: confirm flag name
            args += ["--provider", self.provider]
        if resume is not None:
            # TODO: confirm resume flag syntax
            args += ["--resume", resume.value]

        return args

    # If Pi supports prompt via stdin:
    def stdin_payload(self, prompt: str, resume: ResumeToken | None, *, state: PiStreamState) -> bytes | None:
        return prompt.encode("utf-8")

    def translate(self, data: dict[str, Any], *, state: PiStreamState, resume: ResumeToken | None, found_session: ResumeToken | None) -> list[TakopiEvent]:
        return translate_pi_event(data, state=state, title=self.session_title)


def build_runner(config: EngineConfig, config_path: Path) -> Runner:
    cmd = config.get("cmd") or "pi"
    if not isinstance(cmd, str):
        raise ConfigError(f"Invalid `pi.cmd` in {config_path}; expected a string.")

    extra_args_value = config.get("extra_args") or []
    if not (isinstance(extra_args_value, list) and all(isinstance(x, str) for x in extra_args_value)):
        raise ConfigError(f"Invalid `pi.extra_args` in {config_path}; expected a list of strings.")
    extra_args = list(extra_args_value)

    model = config.get("model")
    if model is not None and not isinstance(model, str):
        raise ConfigError(f"Invalid `pi.model` in {config_path}; expected a string.")

    provider = config.get("provider")
    if provider is not None and not isinstance(provider, str):
        raise ConfigError(f"Invalid `pi.provider` in {config_path}; expected a string.")

    title = str(config.get("session_title") or (model if model else "pi"))

    return PiRunner(pi_cmd=cmd, extra_args=extra_args, model=model, provider=provider, session_title=title)


BACKEND = EngineBackend(
    id="pi",
    build_runner=build_runner,
    cli_cmd="pi",  # change to actual executable if needed
    install_cmd="npm install -g @mariozechner/pi-coding-agent",
)
```

Key point: the only Pi-specific unknowns are the actual flag spellings; the rest is standard Takopi runner plumbing.

---

# 8) Add tests (mirroring Claude/Codex patterns)

Create:

* `tests/test_pi_runner.py`
* `tests/fixtures/pi_stream_success.jsonl`
* `tests/fixtures/pi_stream_error.jsonl`

## 8.1 How to record fixtures

Use your chosen headless JSON mode and capture stdout:

```bash
pi <headless-json-args> <<'EOF' > tests/fixtures/pi_stream_success.jsonl
Write a file called notes.md with the text hello
EOF
```

Then:

* scrub secrets
* keep it **small**
* ensure it includes:

  * a session start line
  * 1–2 tool calls
  * final result

## 8.2 What to test

1. Resume codec:

* `runner.format_resume(token)` matches canonical command
* `runner.extract_resume(text)` picks up the token
* ignores other engines’ resume lines

2. Translation:

* first `StartedEvent` contains `engine="pi"` + correct resume id
* tool actions produce stable ids and have correct `kind` (`command` / `file_change` / `tool`)
* `CompletedEvent` is last and contains final answer

3. Locking semantics:

* replicate the “serialize after session is known” test pattern used for Codex/Claude
* use a tiny fake `pi` script that prints JSON lines and blocks until a gate file exists

---

# 9) Documentation updates you should ship with the runner

Takopi’s repo already includes per-engine runner docs in `docs/runner/*`. Add:

* `docs/runner/pi/pi-runner.md` (like `docs/runner/claude/claude-runner.md`)
* `docs/runner/pi/pi-takopi-events.md` (examples of Pi JSONL → Takopi events)
* `docs/runner/pi/pi-stream-json-cheatsheet.md` (how to capture JSONL and common event types)

Also update:

* `docs/developing.md` (list `pi` in “available engines” and mention install/auth path)
* optional: README / onboarding messaging (engine selection panel already auto-discovers)

---

# 10) Practical pitfalls and how to handle them

## 10.1 Pi prints non-JSON to stdout

Takopi’s `iter_jsonl()` treats invalid JSON lines as “bad” and your runner will emit a warning action (if you keep default behavior). Best practice is to configure Pi so stdout is JSON-only.

If Pi can’t guarantee that, consider:

* redirecting Pi logs to stderr (if flags exist)
* filtering lines that don’t begin with `{` (as a last resort; less ideal)

## 10.2 Session compaction/events you don’t care about

Pi emits internal events like compaction in session logs. Ignore them unless they’re user-facing.

## 10.3 Auth/config drift

Pi recently changed auth storage layout to `~/.pi/agent/auth.json`. This is a common “works on my machine” footgun; call it out in your docs and in error messages.

---

# 11) Final checklist

* [ ] `src/takopi/runners/pi.py` added and exports `BACKEND`
* [ ] `EngineBackend(id="pi")` has correct `install_cmd` and `cli_cmd`
* [ ] `PiRunner` emits `StartedEvent` once session is known
* [ ] Resume line format matches Pi CLI’s real resume command
* [ ] JSONL parsing is robust (unknown events ignored; errors surfaced as warnings)
* [ ] `CompletedEvent` is always emitted (success/failure) and last
* [ ] Tests cover resume parsing, translation, and lock semantics
* [ ] Docs added under `docs/runner/pi/`

---

If you want, I can also draft the actual **`docs/runner/pi/pi-runner.md`** file in the same style as the existing Claude/Codex runner docs (with a concrete “event mapping” section), using the skeleton above and leaving only the exact Pi flag names as TODOs.
