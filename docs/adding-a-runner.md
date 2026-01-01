# Adding a Runner

This guide walks through adding a new engine to Takopi without changing the
domain model. Use the existing runners (Codex/Claude) as references.

## Quick checklist

1. Implement `Runner` in `src/takopi/runners/<engine>.py`.
2. Emit Takopi events from `takopi.model` and implement resume helpers
   (`format_resume`, `extract_resume`, `is_resume_line`).
3. Define `BACKEND = EngineBackend(...)` in the runner module (auto-discovered).
4. Extend tests (runner contract + engine-specific translation tests).

---

## Example: adding a `pi` engine

This is a concrete walkthrough for an imaginary CLI called `pi`. The goal is to
make it easy to drop in another engine without changing the Takopi domain model.

### 1) Decide engine identity + resume format

- Engine id: `"pi"` (used in config, resume tokens, and CLI subcommand).
- Canonical resume line: the engine’s own CLI resume command, e.g.
  `` `pi --resume <session_id>` ``.
- If your engine uses the standard `"<engine> resume <token>"` format, you can
  reuse `compile_resume_pattern()`. Otherwise, define a custom regex in the
  runner (like Claude does).

### 2) Implement `src/takopi/runners/pi.py`

Skeleton outline:

```py
ENGINE: EngineId = "pi"
_RESUME_RE = re.compile(r"(?im)^\s*`?pi\s+--resume\s+(?P<token>[^`\\s]+)`?\\s*$")

@dataclass
class PiRunner(BaseRunner):
    engine: EngineId = ENGINE
    resume_re: re.Pattern[str] = _RESUME_RE

    pi_cmd: str = "pi"
    model: str | None = None
    allowed_tools: list[str] | None = None

    def _build_args(self, prompt: str, resume: ResumeToken | None) -> list[str]:
        args = ["--jsonl"]
        if resume is not None:
            args.extend(["--resume", resume.value])
        if self.model is not None:
            args.extend(["--model", self.model])
        if self.allowed_tools:
            args.extend(["--allowed-tools", ",".join(self.allowed_tools)])
        args.append("--")
        args.append(prompt)
        return args

    async def run_impl(
        self, prompt: str, resume: ResumeToken | None
    ) -> AsyncIterator[TakopiEvent]:
        ...
```

Key implementation notes:

- Use `BaseRunner` for resume parsing + per-session serialization.
- Set `resume_re` only if your engine uses a non-standard resume format
  (otherwise `BaseRunner` uses `compile_resume_pattern(...)` automatically).
- Use `iter_jsonl(...)` + `drain_stderr(...)` from `takopi.utils.streams`.
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
    check_setup=check_setup,
    build_runner=build_runner,
    startup_message=startup_message,
)
```

No changes to `engines.py` or `cli.py` are required.

Only modules that define `BACKEND` are treated as engines. Internal/testing
modules (like `mock.py`) should omit it.

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
