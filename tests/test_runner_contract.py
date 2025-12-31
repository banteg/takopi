import anyio
import pytest
from collections.abc import AsyncGenerator
from typing import cast

from takopi.model import EngineId, ResumeToken, TakopiEvent
from takopi.runners.mock import Emit, Return, ScriptRunner, Wait
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
    seen = [evt async for evt in runner.run("hi", None)]

    session_events = [evt for evt in seen if evt["type"] == "session.started"]
    assert len(session_events) == 1

    completed_events = [evt for evt in seen if evt["type"] == "run.completed"]
    assert len(completed_events) == 1
    assert seen[-1]["type"] == "run.completed"

    session_idx = seen.index(session_events[0])
    completed_idx = seen.index(completed_events[0])
    assert session_idx < completed_idx
    assert completed_events[0]["resume"] == session_events[0]["resume"]
    assert completed_events[0]["answer"] == "done"

    assert [evt["type"] for evt in seen if evt["type"] not in {"session.started", "run.completed"}] == [
        "action.started",
        "action.completed",
    ]

    completed_event = next(evt for evt in seen if evt["type"] == "action.completed")
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
    seen = [evt async for evt in runner.run("hello", None)]
    session = next(evt for evt in seen if evt["type"] == "session.started")
    completed = next(evt for evt in seen if evt["type"] == "run.completed")
    assert completed["resume"] == session["resume"]
    assert isinstance(completed["resume"], ResumeToken)


@pytest.mark.anyio
async def test_runner_releases_lock_when_consumer_closes() -> None:
    gate = anyio.Event()
    runner = ScriptRunner([Wait(gate)], engine=CODEX_ENGINE, resume_value="sid")

    gen = cast(AsyncGenerator[TakopiEvent, None], runner.run("hello", None))
    try:
        while True:
            evt = await anext(gen)
            if evt["type"] == "session.started":
                break
    finally:
        await gen.aclose()

    gen2 = cast(
        AsyncGenerator[TakopiEvent, None],
        runner.run("again", ResumeToken(engine=CODEX_ENGINE, value="sid")),
    )
    try:
        while True:
            evt2 = await anext(gen2)
            if evt2["type"] == "session.started":
                break
    finally:
        await gen2.aclose()
