# Adding a Runner

This guide walks through adding a new engine to Takopi without changing the
domain model. Use the existing runners (Codex/Claude) as references.

## Quick checklist

1. Implement `Runner` in `src/takopi/runners/<engine>.py` (usually via
   `JsonlSubprocessRunner`).
2. Emit Takopi events from `takopi.model` and implement resume helpers
   (`format_resume`, `extract_resume`, `is_resume_line`).
3. Define `BACKEND = EngineBackend(...)` in the runner module (auto-discovered),
   including `install_cmd` (and `cli_cmd` only if the binary name differs).
4. Extend tests (runner contract + engine-specific translation tests).

---

## Example: adding a `pi` engine

This is a concrete walkthrough for an imaginary CLI called `pi`. The goal is to
make it easy to drop in another engine without changing the Takopi domain model.

### 1) Decide engine identity + resume format

- Engine id: `"pi"` (used in config, resume tokens, and CLI subcommand).
- Canonical resume line: the engine’s own CLI resume command, e.g.
  `` `pi --resume <session_id>` ``.
- Pick the resume line format you want to support and define a regex for it in
  the runner (Claude is a good example). If you choose the
  `"<engine> resume <token>"` shape, you can use that exact regex.

### 2) Implement `src/takopi/runners/pi.py`

Recommended: `JsonlSubprocessRunner`

For JSONL CLIs, this base class centralizes subprocess + JSONL plumbing,
lock timing, and completion semantics. Your runner usually only needs:

- `command()` (binary name)
- `build_args(...)`
- `translate(...)` (map one JSON object to a list of Takopi events)

Optional hooks for common variants:

- `stdin_payload(...)`: return `None` if the prompt is passed via argv
- `env(...)`: add or redact environment variables
- `invalid_json_events(...)`: customize the warning event
- `process_error_events(...)`: customize `rc != 0` handling
- `stream_end_events(...)`: customize stream-end fallback (no `CompletedEvent`)
- `handle_started_event(...)`: customize session-id validation

If you call `note_event(...)`, your state object must include `note_seq` or
override `next_note_id(...)`.

Skeleton outline (JSONL CLI):

```py
ENGINE: EngineId = "pi"
_RESUME_RE = re.compile(r"(?im)^\s*`?pi\s+--resume\s+(?P<token>[^`\\s]+)`?\\s*$")

@dataclass
class PiRunner(ResumeTokenMixin, JsonlSubprocessRunner):
    engine: EngineId = ENGINE
    resume_re: re.Pattern[str] = _RESUME_RE

    pi_cmd: str = "pi"
    model: str | None = None
    allowed_tools: list[str] | None = None

    def command(self) -> str:
        return self.pi_cmd

    def build_args(
        self, prompt: str, resume: ResumeToken | None, *, state: Any
    ) -> list[str]:
        _ = prompt, state
        args = ["--jsonl", "--verbose"]
        if resume is not None:
            args.extend(["--resume", resume.value])
        if self.model is not None:
            args.extend(["--model", self.model])
        if self.allowed_tools:
            args.extend(["--allowed-tools", ",".join(self.allowed_tools)])
        return args

    def stdin_payload(
        self, prompt: str, resume: ResumeToken | None, *, state: Any
    ) -> bytes | None:
        _ = resume, state
        return prompt.encode()

    def translate(
        self,
        data: dict[str, Any],
        *,
        state: Any,
        resume: ResumeToken | None,
        found_session: ResumeToken | None,
    ) -> list[TakopiEvent]:
        _ = state, resume, found_session
        ...
```

Key implementation notes:

- Use `BaseRunner` (or `JsonlSubprocessRunner`) for per-session serialization.
- Mix in `ResumeTokenMixin` (with a `resume_re`) or override
  `format_resume` / `extract_resume` / `is_resume_line` so the runner owns
  resume encoding/decoding.
- For JSONL CLIs, prefer `JsonlSubprocessRunner` and implement `command`,
  `build_args`, and `translate` (override `stdin_payload` if the prompt should
  be passed via argv instead of stdin).
- If you don’t use `JsonlSubprocessRunner`, use `iter_jsonl(...)` +
  `drain_stderr(...)` from `takopi.utils.streams`.
- **Minimal mode is supported:** start with exactly one `StartedEvent` and one
  `CompletedEvent`. `ActionEvent`s are optional and can be added later. If you
  do emit actions, you can emit only `phase="completed"` notes without tracking
  pending state.
- **Do not truncate** tool outputs in the runner; pass full strings into events.
  Truncation belongs in renderers.

### 3) Map Pi JSONL → Takopi events

Example Pi lines (imaginary):

```json
{"type":"session.start","session_id":"pi_01","model":"pi-large"}
{"type":"tool.use","id":"toolu_1","name":"Bash","input":{"command":"ls"}}
{"type":"tool.result","tool_use_id":"toolu_1","content":"ok","is_error":false}
{"type":"final","session_id":"pi_01","ok":true,"answer":"Done."}
```

Mapping guidance:

- `session.start` → `StartedEvent(engine="pi", resume=<session_id>, title=<model>)`
- `tool.use` → `ActionEvent(phase="started")`
- `tool.result` → `ActionEvent(phase="completed")` and **pop** pending actions
- `final` → `CompletedEvent(ok, answer, resume)` (emit **exactly one**)

If Pi emits warnings/errors before the final event, surface them as completed
`ActionEvent`s (e.g., `kind="warning"`).

### 4) Expose the backend (auto-discovered)

Takopi discovers runners by importing modules in `takopi.runners` and looking
for a module-level `BACKEND: EngineBackend` (from `takopi.backends`).

At the bottom of `src/takopi/runners/pi.py`, define:

```py
BACKEND = EngineBackend(
    id="pi",
    build_runner=build_runner,
    install_cmd="npm install -g @acme/pi-cli",
)
```

No changes to `engines.py` or `cli.py` are required.

Only modules that define `BACKEND` are treated as engines. Internal/testing
modules (like `mock.py`) should omit it.

If the CLI binary name differs from the engine id, set `cli_cmd="pi-cli"` on
the backend.

Example config (minimal):

```toml
[pi]
model = "pi-large"
allowed_tools = ["Bash", "Read"]
```

### 5) Tests + fixtures

- Add `tests/test_pi_runner.py` for translation behavior.
- Reuse `tests/test_runner_contract.py` to ensure lock/resume invariants.
- Add JSONL fixtures under `tests/fixtures/` for the Pi stream.
