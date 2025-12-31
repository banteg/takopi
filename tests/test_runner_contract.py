import pytest
from typing import cast

from takopi.model import EngineId, ResumeToken, TakopiEvent
from takopi.runners.mock import Emit, Return, ScriptRunner
from tests.factories import action_started

CODEX_ENGINE = EngineId("codex")


@pytest.mark.anyio
async def test_runner_contract_session_started_and_order() -> None:
    raw_completed: TakopiEvent = cast(
        TakopiEvent,
        {
            "type": "action.completed",
            "engine": CODEX_ENGINE,
            "action": {
                "id": "a-1",
                "kind": "command",
                "title": "echo ok",
                "detail": {"exit_code": 0},
            },
        },
    )
    script = [
        Emit(action_started("a-1", "command", "echo ok")),
        Emit(raw_completed),
        Return(answer="done"),
    ]
    runner = ScriptRunner(script, engine=CODEX_ENGINE, resume_value="abc123")
    seen: list[TakopiEvent] = []

    async def on_event(event: TakopiEvent) -> None:
        seen.append(event)

    result = await runner.run("hi", None, on_event)

    session_events = [evt for evt in seen if evt["type"] == "session.started"]
    assert len(session_events) == 1
    assert seen[0]["type"] == "session.started"
    assert result.resume == session_events[0]["resume"]
    assert [evt["type"] for evt in seen[1:]] == ["action.started", "action.completed"]

    completed_event = seen[-1]
    assert completed_event["type"] == "action.completed"
    assert completed_event.get("ok") is True
    action = completed_event["action"]
    assert action.get("id") == "a-1"
    assert action.get("kind") == "command"
    assert action.get("title") == "echo ok"


@pytest.mark.anyio
async def test_runner_contract_resume_matches_session_started() -> None:
    runner = ScriptRunner(
        [Return(answer="ok")], engine=CODEX_ENGINE, resume_value="sid"
    )
    seen: list[TakopiEvent] = []

    async def on_event(event: TakopiEvent) -> None:
        seen.append(event)

    result = await runner.run("hello", None, on_event)
    session = next(evt for evt in seen if evt["type"] == "session.started")
    assert result.resume == session["resume"]
    assert isinstance(result.resume, ResumeToken)


@pytest.mark.anyio
async def test_runner_aborts_on_event_error() -> None:
    runner = ScriptRunner([Return(answer="ok")], engine=CODEX_ENGINE)

    async def on_event(_event: TakopiEvent) -> None:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        await runner.run("hello", None, on_event)
