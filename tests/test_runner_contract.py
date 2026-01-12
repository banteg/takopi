import anyio
import pytest
from collections.abc import AsyncGenerator
from typing import cast

from takopi.model import (
    Action,
    ActionEvent,
    CompletedEvent,
    ResumeToken,
    StartedEvent,
    TakopiEvent,
)
from takopi.runners.mock import Emit, Return, ScriptRunner, Wait
from tests.factories import action_started

CODEX_ENGINE = "codex"


@pytest.mark.anyio
async def test_runner_contract_session_started_and_order() -> None:
    raw_completed: TakopiEvent = ActionEvent(
        engine=CODEX_ENGINE,
        action=Action(
            id="a-1",
            kind="command",
            title="echo ok",
            detail={"exit_code": 0},
        ),
        phase="completed",
    )
    script = [
        Emit(action_started("a-1", "command", "echo ok")),
        Emit(raw_completed),
        Return(answer="done"),
    ]
    runner = ScriptRunner(script, engine=CODEX_ENGINE, resume_value="abc123")
    seen = [evt async for evt in runner.run("hi", None)]

    session_events = [evt for evt in seen if isinstance(evt, StartedEvent)]
    assert len(session_events) == 1

    completed_events = [evt for evt in seen if isinstance(evt, CompletedEvent)]
    assert len(completed_events) == 1
    assert seen[-1].type == "completed"

    session_idx = seen.index(session_events[0])
    completed_idx = seen.index(completed_events[0])
    assert session_idx < completed_idx
    assert completed_events[0].resume == session_events[0].resume
    assert completed_events[0].answer == "done"

    assert [evt.type for evt in seen if evt.type not in {"started", "completed"}] == [
        "action",
        "action",
    ]

    completed_event = next(
        evt for evt in seen if isinstance(evt, ActionEvent) and evt.phase == "completed"
    )
    assert completed_event.type == "action"
    assert completed_event.ok is True
    action = completed_event.action
    assert action.id == "a-1"
    assert action.kind == "command"
    assert action.title == "echo ok"


@pytest.mark.anyio
async def test_runner_contract_resume_matches_session_started() -> None:
    runner = ScriptRunner(
        [Return(answer="ok")], engine=CODEX_ENGINE, resume_value="sid"
    )
    seen = [evt async for evt in runner.run("hello", None)]
    session = next(evt for evt in seen if isinstance(evt, StartedEvent))
    completed = next(evt for evt in seen if isinstance(evt, CompletedEvent))
    assert completed.resume == session.resume
    assert isinstance(completed.resume, ResumeToken)


@pytest.mark.anyio
async def test_runner_releases_lock_when_consumer_closes() -> None:
    gate = anyio.Event()
    runner = ScriptRunner([Wait(gate)], engine=CODEX_ENGINE, resume_value="sid")

    gen = cast(AsyncGenerator[TakopiEvent], runner.run("hello", None))
    try:
        while True:
            evt = await anext(gen)
            if isinstance(evt, StartedEvent):
                break
    finally:
        await gen.aclose()

    gen2 = cast(
        AsyncGenerator[TakopiEvent],
        runner.run("again", ResumeToken(engine=CODEX_ENGINE, value="sid")),
    )
    try:
        while True:
            evt2 = await anext(gen2)
            if isinstance(evt2, StartedEvent):
                break
    finally:
        await gen2.aclose()
