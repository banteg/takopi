from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import anyio
import pytest

from takopi.events import EventFactory
from takopi.model import ActionEvent, CompletedEvent, ResumeToken, StartedEvent
from takopi.runners.cursor import (
    ENGINE,
    CursorRunner,
    CursorRunState,
    translate_cursor_event,
)
from takopi.schemas import cursor as cursor_schema


def _load_fixture(name: str) -> list[cursor_schema.CursorEvent]:
    path = Path(__file__).parent / "fixtures" / name
    events: list[cursor_schema.CursorEvent] = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            decoded = cursor_schema.decode_event(line)
        except Exception as exc:  # noqa: BLE001
            raise AssertionError(f"{name} contained unparseable line: {line}") from exc
        events.append(decoded)
    return events


def test_cursor_resume_format_and_extract() -> None:
    runner = CursorRunner(model=None, workspace=None)
    token = ResumeToken(engine=ENGINE, value="abc-123-def")

    assert runner.format_resume(token) == "`agent --resume abc-123-def`"
    assert runner.extract_resume("`agent --resume abc-123-def`") == token
    assert runner.extract_resume("agent --resume abc-123-def") == token
    assert runner.extract_resume("`claude --resume xyz`") is None


def test_build_args_new_session() -> None:
    runner = CursorRunner(model=None, workspace="/home/user/project")
    state = CursorRunState(factory=runner.new_state("hello", None).factory)

    with patch("takopi.runners.cursor.get_run_options", return_value=None):
        args = runner.build_args("hello", None, state=state)

    # build_args returns args for script: -qfc, quoted agent cmd, /dev/null
    assert args[0] == "-qfc"
    assert args[-1] == "/dev/null"
    arg_str = args[1]
    assert "agent" in arg_str
    assert "-p" in arg_str
    assert "--workspace" in arg_str
    assert "/home/user/project" in arg_str
    assert "hello" in arg_str


def test_build_args_with_resume() -> None:
    runner = CursorRunner(model=None, workspace=None)
    resume = ResumeToken(engine=ENGINE, value="session-abc-123")
    state = CursorRunState(factory=runner.new_state("hi", resume).factory)

    with patch("takopi.runners.cursor.get_run_options", return_value=None):
        args = runner.build_args("hi", resume, state=state)

    arg_str = " ".join(args)
    assert "--resume" in arg_str
    assert "session-abc-123" in arg_str


def test_build_args_with_model() -> None:
    runner = CursorRunner(model="Claude-4-Opus", workspace=None)
    state = CursorRunState(factory=runner.new_state("hi", None).factory)

    with patch("takopi.runners.cursor.get_run_options", return_value=None):
        args = runner.build_args("hi", None, state=state)

    arg_str = " ".join(args)
    assert "--model" in arg_str
    assert "Claude-4-Opus" in arg_str


def test_translate_success_fixture() -> None:
    state = CursorRunState(factory=EventFactory(ENGINE))
    events: list = []
    for event in _load_fixture("cursor_stream_success.jsonl"):
        events.extend(
            translate_cursor_event(
                event, title="Cursor", state=state, factory=state.factory
            )
        )

    started = next(evt for evt in events if isinstance(evt, StartedEvent))
    assert started.resume.value == "a1b2c3d4-e5f6-7890-abcd-ef1234567890"

    action_events = [evt for evt in events if isinstance(evt, ActionEvent)]
    assert (
        len(action_events) >= 2
    )  # thinking started+completed, tool read started+completed

    completed = next(evt for evt in events if isinstance(evt, CompletedEvent))
    assert completed.ok is True
    assert "I found README.md" in (completed.answer or "")
    assert completed.resume == started.resume


def test_translate_error_fixture() -> None:
    state = CursorRunState(factory=EventFactory(ENGINE))
    events: list = []
    for event in _load_fixture("cursor_stream_error.jsonl"):
        events.extend(
            translate_cursor_event(
                event, title="Cursor", state=state, factory=state.factory
            )
        )

    completed = next(evt for evt in events if isinstance(evt, CompletedEvent))
    assert completed.ok is False
    assert "Request failed" in (completed.error or completed.answer or "")


def test_translate_thinking_blocks() -> None:
    state = CursorRunState(factory=EventFactory(ENGINE))
    events: list = []

    # Simulate thinking delta + completed, then assistant, then result
    thinking_delta = cursor_schema.Thinking(
        subtype="delta",
        text="Analyzing the request...",
        session_id="test-session",
        timestamp_ms=1000,
    )
    thinking_complete = cursor_schema.Thinking(
        subtype="completed",
        text=None,
        session_id="test-session",
        timestamp_ms=1100,
    )
    assistant = cursor_schema.AssistantResponse(
        message=cursor_schema.AssistantMessage(
            role="assistant",
            content=[
                cursor_schema.TextContent(type="text", text="Here is the answer.")
            ],
        ),
        session_id="test-session",
    )
    result = cursor_schema.Result(
        subtype="success",
        result="Here is the answer.",
        session_id="test-session",
        duration_ms=500,
        duration_api_ms=400,
        is_error=False,
    )

    for evt in [thinking_delta, thinking_complete, assistant, result]:
        events.extend(
            translate_cursor_event(
                evt, title="Cursor", state=state, factory=state.factory
            )
        )

    completed = next(evt for evt in events if isinstance(evt, CompletedEvent))
    assert completed.ok is True
    assert "> **" in (completed.answer or "")
    assert "Thinking" in (completed.answer or "")
    assert "Analyzing the request" in (completed.answer or "")
    assert "Here is the answer" in (completed.answer or "")


@pytest.mark.anyio
async def test_run_serializes_same_session() -> None:
    runner = CursorRunner(model=None, workspace=None)
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
                resume=ResumeToken(engine=ENGINE, value="session-123"),
                ok=True,
                answer="ok",
            )
        finally:
            in_flight -= 1

    runner.run_impl = run_stub  # type: ignore[assignment]

    async def drain(prompt: str, resume: ResumeToken | None) -> None:
        async for _event in runner.run(prompt, resume):
            pass

    token = ResumeToken(engine=ENGINE, value="session-123")
    async with anyio.create_task_group() as tg:
        tg.start_soon(drain, "a", token)
        tg.start_soon(drain, "b", token)
        await anyio.sleep(0)
        gate.set()
    assert max_in_flight == 1
