import json
from pathlib import Path
from typing import cast

import pytest

import takopi.runners.kimi as kimi_runner
from takopi.model import ActionEvent, CompletedEvent, ResumeToken, StartedEvent
from takopi.runners.kimi import (
    ENGINE,
    KimiRunner,
    KimiStreamState,
    translate_kimi_event,
)
from takopi.schemas import kimi as kimi_schema


def _load_fixture(name: str) -> list[kimi_schema.StreamJsonMessage]:
    path = Path(__file__).parent / "fixtures" / name
    events = [
        kimi_schema.decode_stream_json_line(line)
        for line in path.read_bytes().splitlines()
        if line.strip()
    ]
    return events


def _decode_event(payload: dict) -> kimi_schema.StreamJsonMessage:
    data = json.dumps(payload).encode("utf-8")
    return kimi_schema.decode_stream_json_line(data)


def test_kimi_resume_format_and_extract() -> None:
    runner = KimiRunner(kimi_cmd="kimi")
    token = ResumeToken(engine=ENGINE, value="sid")

    assert runner.format_resume(token) == "`kimi --session sid`"
    assert runner.extract_resume("`kimi --session sid`") == token
    assert runner.extract_resume("kimi -S other") == ResumeToken(
        engine=ENGINE, value="other"
    )
    assert runner.extract_resume("`claude --resume sid`") is None


def test_build_runner_uses_shutil_which(monkeypatch) -> None:
    expected = "/usr/local/bin/kimi"
    called: dict[str, str] = {}

    def fake_which(name: str) -> str | None:
        called["name"] = name
        return expected

    monkeypatch.setattr(kimi_runner.shutil, "which", fake_which)
    runner = cast(KimiRunner, kimi_runner.build_runner({}, Path("takopi.toml")))

    assert called["name"] == "kimi"
    assert runner.kimi_cmd == expected


def test_translate_fixture() -> None:
    state = KimiStreamState()
    events: list = []
    for event in _load_fixture("kimi_stream_json_session.jsonl"):
        events.extend(
            translate_kimi_event(
                event,
                title="kimi",
                state=state,
                factory=state.factory,
            )
        )

    # Should have a started event (from first assistant message)
    assert isinstance(events[0], StartedEvent)
    started = next(evt for evt in events if isinstance(evt, StartedEvent))
    assert started.engine == ENGINE

    # Should have action events for tool calls and results
    action_events = [evt for evt in events if isinstance(evt, ActionEvent)]
    assert len(action_events) == 4  # 2 tool starts + 2 tool completions

    started_actions = {
        (evt.action.id, evt.phase): evt
        for evt in action_events
        if evt.phase == "started"
    }
    # Shell command should be detected as "command" kind
    assert started_actions[("tc_001", "started")].action.kind == "command"
    # Write should be detected as "file_change" kind
    write_action = started_actions[("tc_002", "started")].action
    assert write_action.kind == "file_change"
    assert write_action.detail["changes"][0]["path"] == "notes.md"

    completed_actions = {
        (evt.action.id, evt.phase): evt
        for evt in action_events
        if evt.phase == "completed"
    }
    assert completed_actions[("tc_001", "completed")].ok is True
    assert completed_actions[("tc_002", "completed")].ok is True

    # Check that state captured the last assistant text
    assert state.last_assistant_text == "Done! I've listed the files and created a notes.md file."


def test_translate_assistant_message_generates_started_event() -> None:
    state = KimiStreamState()
    event = _decode_event({"role": "assistant", "content": "Hello!"})

    events = translate_kimi_event(
        event,
        title="kimi",
        state=state,
        factory=state.factory,
    )

    assert len(events) == 1
    assert isinstance(events[0], StartedEvent)
    assert events[0].engine == ENGINE
    assert state.did_start is True


def test_translate_subsequent_assistant_message_no_started() -> None:
    state = KimiStreamState()
    state.did_start = True
    state.session_id = "test-session"
    event = _decode_event({"role": "assistant", "content": "More text"})

    events = translate_kimi_event(
        event,
        title="kimi",
        state=state,
        factory=state.factory,
    )

    # Should not emit another StartedEvent
    assert len(events) == 0
    assert state.last_assistant_text == "More text"


def test_translate_tool_call_generates_action_started() -> None:
    state = KimiStreamState()
    state.did_start = True
    state.session_id = "test-session"
    event = _decode_event({
        "role": "assistant",
        "content": "Let me check.",
        "tool_calls": [{
            "type": "function",
            "id": "tc_1",
            "function": {"name": "Shell", "arguments": '{"command":"ls"}'}
        }]
    })

    events = translate_kimi_event(
        event,
        title="kimi",
        state=state,
        factory=state.factory,
    )

    assert len(events) == 1
    assert isinstance(events[0], ActionEvent)
    assert events[0].phase == "started"
    assert events[0].action.id == "tc_1"
    assert events[0].action.kind == "command"


def test_translate_tool_message_generates_action_completed() -> None:
    state = KimiStreamState()
    state.did_start = True
    state.session_id = "test-session"
    # Pre-populate pending action
    from takopi.model import Action
    state.pending_actions["tc_1"] = Action(
        id="tc_1",
        kind="command",
        title="ls",
        detail={"name": "Shell", "input": {"command": "ls"}},
    )

    event = _decode_event({
        "role": "tool",
        "tool_call_id": "tc_1",
        "content": "file1.txt\nfile2.txt"
    })

    events = translate_kimi_event(
        event,
        title="kimi",
        state=state,
        factory=state.factory,
    )

    assert len(events) == 1
    assert isinstance(events[0], ActionEvent)
    assert events[0].phase == "completed"
    assert events[0].action.id == "tc_1"
    assert events[0].ok is True


def test_translate_user_message_no_events() -> None:
    state = KimiStreamState()
    event = _decode_event({"role": "user", "content": "Hello"})

    events = translate_kimi_event(
        event,
        title="kimi",
        state=state,
        factory=state.factory,
    )

    assert events == []


def test_stream_end_events_with_last_text() -> None:
    runner = KimiRunner(kimi_cmd="kimi")
    state = KimiStreamState()
    state.last_assistant_text = "Final answer"
    state.session_id = "test-session"

    events = runner.stream_end_events(
        resume=None,
        found_session=None,
        state=state,
    )

    assert len(events) == 1
    assert isinstance(events[0], CompletedEvent)
    assert events[0].ok is True
    assert events[0].answer == "Final answer"


def test_stream_end_events_no_text() -> None:
    runner = KimiRunner(kimi_cmd="kimi")
    state = KimiStreamState()

    events = runner.stream_end_events(
        resume=None,
        found_session=None,
        state=state,
    )

    assert len(events) == 1
    assert isinstance(events[0], CompletedEvent)
    assert events[0].ok is False
    assert "no session_id was captured" in events[0].error


def test_build_args_basic() -> None:
    runner = KimiRunner(kimi_cmd="kimi")
    args = runner._build_args("hello", None)

    assert "--print" in args
    assert "--output-format" in args
    assert "stream-json" in args
    assert "-p" in args
    assert "hello" in args


def test_build_args_with_resume() -> None:
    runner = KimiRunner(kimi_cmd="kimi")
    resume = ResumeToken(engine=ENGINE, value="session-123")
    args = runner._build_args("hello", resume)

    assert "--session" in args
    assert "session-123" in args


def test_build_args_with_model() -> None:
    runner = KimiRunner(kimi_cmd="kimi", model="kimi-k2")
    args = runner._build_args("hello", None)

    assert "--model" in args
    assert "kimi-k2" in args


def test_build_args_with_extra_args() -> None:
    runner = KimiRunner(kimi_cmd="kimi", extra_args=["--yolo", "--thinking"])
    args = runner._build_args("hello", None)

    assert "--yolo" in args
    assert "--thinking" in args
