import json
from pathlib import Path

import anyio
import pytest

from takopi.model import ActionEvent, CompletedEvent, ResumeToken, StartedEvent
from takopi.runners.opencode import (
    OpenCodeRunner,
    OpenCodeStreamState,
    ENGINE,
    translate_opencode_event,
)
from takopi.schemas import opencode as opencode_schema


def _load_fixture(name: str) -> list[opencode_schema.OpenCodeEvent]:
    path = Path(__file__).parent / "fixtures" / name
    events: list[opencode_schema.OpenCodeEvent] = []
    for line in path.read_bytes().splitlines():
        if not line.strip():
            continue
        try:
            events.append(opencode_schema.decode_event(line))
        except Exception as exc:
            raise AssertionError(
                f"{name} contained unparseable line: {line!r}"
            ) from exc
    return events


def _decode_event(payload: dict) -> opencode_schema.OpenCodeEvent:
    return opencode_schema.decode_event(json.dumps(payload).encode("utf-8"))


def test_opencode_resume_format_and_extract() -> None:
    runner = OpenCodeRunner(opencode_cmd="opencode")
    token = ResumeToken(engine=ENGINE, value="ses_abc123")

    assert runner.format_resume(token) == "`opencode --session ses_abc123`"
    assert runner.extract_resume("`opencode --session ses_abc123`") == token
    assert runner.extract_resume("opencode run -s ses_other") == ResumeToken(
        engine=ENGINE, value="ses_other"
    )
    assert runner.extract_resume("opencode -s ses_other") == ResumeToken(
        engine=ENGINE, value="ses_other"
    )
    assert runner.extract_resume("`claude --resume sid`") is None
    assert runner.extract_resume("`codex resume sid`") is None


def test_translate_success_fixture() -> None:
    state = OpenCodeStreamState()
    events: list = []
    for event in _load_fixture("opencode_stream_success.jsonl"):
        events.extend(translate_opencode_event(event, title="opencode", state=state))

    assert isinstance(events[0], StartedEvent)
    started = next(evt for evt in events if isinstance(evt, StartedEvent))
    assert started.resume.value == "ses_494719016ffe85dkDMj0FPRbHK"
    assert started.resume.engine == ENGINE

    action_events = [evt for evt in events if isinstance(evt, ActionEvent)]
    assert len(action_events) == 1

    completed_actions = [evt for evt in action_events if evt.phase == "completed"]
    assert len(completed_actions) == 1
    assert completed_actions[0].action.kind == "command"
    assert completed_actions[0].ok is True

    completed = next(evt for evt in events if isinstance(evt, CompletedEvent))
    assert events[-1] == completed
    assert completed.ok is True
    assert completed.resume == started.resume
    assert completed.answer == "```\nhello\n```"


def test_translate_missing_reason_success() -> None:
    state = OpenCodeStreamState()
    events: list = []
    for event in _load_fixture("opencode_stream_success_no_reason.jsonl"):
        events.extend(translate_opencode_event(event, title="opencode", state=state))

    started = next(evt for evt in events if isinstance(evt, StartedEvent))
    runner = OpenCodeRunner(opencode_cmd="opencode")
    fallback = runner.stream_end_events(
        resume=None,
        found_session=started.resume,
        state=state,
    )

    completed = next(evt for evt in fallback if isinstance(evt, CompletedEvent))
    assert completed.ok is True
    assert completed.resume == started.resume
    assert completed.answer == "All done."


def test_translate_accumulates_text() -> None:
    state = OpenCodeStreamState()

    events = translate_opencode_event(
        _decode_event({"type": "step_start", "sessionID": "ses_test123", "part": {}}),
        title="opencode",
        state=state,
    )
    assert len(events) == 1
    assert isinstance(events[0], StartedEvent)

    translate_opencode_event(
        _decode_event(
            {
                "type": "text",
                "sessionID": "ses_test123",
                "part": {"type": "text", "text": "Hello "},
            }
        ),
        title="opencode",
        state=state,
    )
    translate_opencode_event(
        _decode_event(
            {
                "type": "text",
                "sessionID": "ses_test123",
                "part": {"type": "text", "text": "World"},
            }
        ),
        title="opencode",
        state=state,
    )

    assert state.last_text == "Hello World"

    events = translate_opencode_event(
        _decode_event(
            {
                "type": "step_finish",
                "sessionID": "ses_test123",
                "part": {"reason": "stop", "tokens": {"input": 100, "output": 10}},
            }
        ),
        title="opencode",
        state=state,
    )

    assert len(events) == 1
    completed = events[0]
    assert isinstance(completed, CompletedEvent)
    assert completed.answer == "Hello World"
    assert completed.ok is True


def test_translate_tool_use_completed() -> None:
    state = OpenCodeStreamState()
    state.session_id = "ses_test123"
    state.emitted_started = True

    events = translate_opencode_event(
        _decode_event(
            {
                "type": "tool_use",
                "sessionID": "ses_test123",
                "part": {
                    "id": "prt_123",
                    "callID": "call_abc",
                    "tool": "bash",
                    "state": {
                        "status": "completed",
                        "input": {"command": "ls -la"},
                        "output": "file1.txt\nfile2.txt",
                        "title": "List files",
                        "metadata": {"exit": 0},
                    },
                },
            }
        ),
        title="opencode",
        state=state,
    )

    assert len(events) == 1
    action_event = events[0]
    assert isinstance(action_event, ActionEvent)
    assert action_event.phase == "completed"
    assert action_event.action.kind == "command"
    assert action_event.action.title == "List files"
    assert action_event.ok is True


def test_translate_tool_use_with_error() -> None:
    state = OpenCodeStreamState()
    state.session_id = "ses_test123"
    state.emitted_started = True

    events = translate_opencode_event(
        _decode_event(
            {
                "type": "tool_use",
                "sessionID": "ses_test123",
                "part": {
                    "id": "prt_123",
                    "callID": "call_abc",
                    "tool": "bash",
                    "state": {
                        "status": "completed",
                        "input": {"command": "exit 1"},
                        "output": "error",
                        "title": "Run failing command",
                        "metadata": {"exit": 1},
                    },
                },
            }
        ),
        title="opencode",
        state=state,
    )

    assert len(events) == 1
    action_event = events[0]
    assert isinstance(action_event, ActionEvent)
    assert action_event.phase == "completed"
    assert action_event.ok is False


def test_translate_tool_use_read_title_wraps_path() -> None:
    state = OpenCodeStreamState()
    state.session_id = "ses_test123"
    state.emitted_started = True
    path = Path.cwd() / "src" / "takopi" / "runners" / "opencode.py"

    events = translate_opencode_event(
        _decode_event(
            {
                "type": "tool_use",
                "sessionID": "ses_test123",
                "part": {
                    "id": "prt_123",
                    "callID": "call_abc",
                    "tool": "read",
                    "state": {
                        "status": "completed",
                        "input": {"filePath": str(path)},
                        "output": "file contents",
                        "title": "src/takopi/runners/opencode.py",
                    },
                },
            }
        ),
        title="opencode",
        state=state,
    )

    assert len(events) == 1
    action_event = events[0]
    assert isinstance(action_event, ActionEvent)
    assert action_event.action.kind == "tool"
    assert action_event.action.title == "`src/takopi/runners/opencode.py`"


def test_translate_error_fixture() -> None:
    state = OpenCodeStreamState()
    events: list = []
    for event in _load_fixture("opencode_stream_error.jsonl"):
        events.extend(translate_opencode_event(event, title="opencode", state=state))

    started = next(evt for evt in events if isinstance(evt, StartedEvent))
    completed = next(evt for evt in events if isinstance(evt, CompletedEvent))

    assert completed.ok is False
    assert completed.error == "Rate limit exceeded"
    assert completed.resume == started.resume


def test_step_finish_tool_calls_does_not_complete() -> None:
    state = OpenCodeStreamState()
    state.session_id = "ses_test123"
    state.emitted_started = True

    events = translate_opencode_event(
        _decode_event(
            {
                "type": "step_finish",
                "sessionID": "ses_test123",
                "part": {
                    "reason": "tool-calls",
                    "tokens": {"input": 100, "output": 10},
                },
            }
        ),
        title="opencode",
        state=state,
    )

    assert len(events) == 0


def test_build_args_new_session() -> None:
    runner = OpenCodeRunner(opencode_cmd="opencode", model="claude-sonnet")
    args = runner.build_args("hello world", None, state=OpenCodeStreamState())

    assert args == [
        "run",
        "--format",
        "json",
        "--model",
        "claude-sonnet",
        "--",
        "hello world",
    ]


def test_build_args_with_resume() -> None:
    runner = OpenCodeRunner(opencode_cmd="opencode")
    token = ResumeToken(engine=ENGINE, value="ses_abc123")
    args = runner.build_args("continue", token, state=OpenCodeStreamState())

    assert args == [
        "run",
        "--format",
        "json",
        "--session",
        "ses_abc123",
        "--",
        "continue",
    ]


def test_stdin_payload_returns_none() -> None:
    runner = OpenCodeRunner(opencode_cmd="opencode")
    payload = runner.stdin_payload("prompt", None, state=OpenCodeStreamState())
    assert payload is None


@pytest.mark.anyio
async def test_run_serializes_same_session() -> None:
    runner = OpenCodeRunner(opencode_cmd="opencode")
    gate = anyio.Event()
    in_flight = 0
    max_in_flight = 0

    async def run_stub(*_args, **_kwargs):
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        try:
            await gate.wait()
            yield CompletedEvent(
                engine=ENGINE,
                resume=ResumeToken(engine=ENGINE, value="ses_test"),
                ok=True,
                answer="ok",
            )
        finally:
            in_flight -= 1

    runner.run_impl = run_stub  # type: ignore[assignment]

    async def drain(prompt: str, resume: ResumeToken | None) -> None:
        async for _event in runner.run(prompt, resume):
            pass

    token = ResumeToken(engine=ENGINE, value="ses_test")
    async with anyio.create_task_group() as tg:
        tg.start_soon(drain, "a", token)
        tg.start_soon(drain, "b", token)
        await anyio.sleep(0)
        gate.set()
    assert max_in_flight == 1
