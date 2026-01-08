from pathlib import Path
from unittest.mock import patch

import anyio
import pytest

from takopi.model import ActionEvent, CompletedEvent, ResumeToken, StartedEvent
from takopi.runners.pi import ENGINE, PiRunner, PiStreamState, translate_pi_event
from takopi.schemas import pi as pi_schema


def _load_fixture(name: str) -> list[pi_schema.PiEvent]:
    path = Path(__file__).parent / "fixtures" / name
    events: list[pi_schema.PiEvent] = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            decoded = pi_schema.decode_event(line)
        except Exception as exc:
            raise AssertionError(f"{name} contained unparseable line: {line}") from exc
        events.append(decoded)
    return events


def test_pi_resume_format_and_extract() -> None:
    runner = PiRunner(
        extra_args=[],
        model=None,
        provider=None,
    )
    token = ResumeToken(engine=ENGINE, value="/tmp/pi/session.jsonl")

    assert runner.format_resume(token) == "`pi --session /tmp/pi/session.jsonl`"
    assert runner.extract_resume("`pi --session /tmp/pi/session.jsonl`") == token
    assert runner.extract_resume('pi --session "/tmp/pi/session.jsonl"') == token
    assert runner.extract_resume("`codex resume sid`") is None

    spaced = ResumeToken(engine=ENGINE, value="/tmp/pi session.jsonl")
    assert runner.format_resume(spaced) == '`pi --session "/tmp/pi session.jsonl"`'
    assert runner.extract_resume('`pi --session "/tmp/pi session.jsonl"`') == spaced


def test_translate_success_fixture() -> None:
    state = PiStreamState(resume=ResumeToken(engine=ENGINE, value="session.jsonl"))
    events: list = []
    for event in _load_fixture("pi_stream_success.jsonl"):
        events.extend(translate_pi_event(event, title="pi", meta=None, state=state))

    assert isinstance(events[0], StartedEvent)
    started = next(evt for evt in events if isinstance(evt, StartedEvent))
    assert started.meta is None

    action_events = [evt for evt in events if isinstance(evt, ActionEvent)]
    assert len(action_events) == 4

    started_actions = {
        (evt.action.id, evt.phase): evt
        for evt in action_events
        if evt.phase == "started"
    }
    assert started_actions[("tool_1", "started")].action.kind == "command"
    write_action = started_actions[("tool_2", "started")].action
    assert write_action.kind == "file_change"
    assert write_action.detail["changes"][0]["path"] == "notes.md"

    completed_actions = {
        (evt.action.id, evt.phase): evt
        for evt in action_events
        if evt.phase == "completed"
    }
    assert completed_actions[("tool_1", "completed")].ok is True
    assert completed_actions[("tool_2", "completed")].ok is True

    completed = next(evt for evt in events if isinstance(evt, CompletedEvent))
    assert events[-1] == completed
    assert completed.ok is True
    assert completed.resume == started.resume
    assert completed.answer == "Done. Added notes.md."


def test_translate_error_fixture() -> None:
    state = PiStreamState(resume=ResumeToken(engine=ENGINE, value="session.jsonl"))
    events: list = []
    for event in _load_fixture("pi_stream_error.jsonl"):
        events.extend(translate_pi_event(event, title="pi", meta=None, state=state))

    completed = next(evt for evt in events if isinstance(evt, CompletedEvent))
    assert completed.ok is False
    assert completed.error == "Upstream error"
    assert completed.answer == "Request failed."


@pytest.mark.anyio
async def test_run_serializes_same_session() -> None:
    runner = PiRunner(
        extra_args=[],
        model=None,
        provider=None,
    )
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
                resume=ResumeToken(engine=ENGINE, value="session.jsonl"),
                ok=True,
                answer="ok",
            )
        finally:
            in_flight -= 1

    runner.run_impl = run_stub  # type: ignore[assignment]

    async def drain(prompt: str, resume: ResumeToken | None) -> None:
        async for _event in runner.run(prompt, resume):
            pass

    token = ResumeToken(engine=ENGINE, value="session.jsonl")
    async with anyio.create_task_group() as tg:
        tg.start_soon(drain, "a", token)
        tg.start_soon(drain, "b", token)
        await anyio.sleep(0)
        gate.set()
    assert max_in_flight == 1


def test_session_path_prefers_run_base_dir(tmp_path: Path) -> None:
    runner = PiRunner(
        extra_args=[],
        model=None,
        provider=None,
    )
    project_cwd = Path("/project")
    session_root = tmp_path / "sessions"

    with (
        patch("takopi.runners.pi.get_run_base_dir", return_value=project_cwd),
        patch(
            "takopi.runners.pi._default_session_dir",
            return_value=session_root,
        ) as default_session_dir,
    ):
        session_path = runner._new_session_path()

    default_session_dir.assert_called_once_with(project_cwd)
    assert str(session_root) in session_path
