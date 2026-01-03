from pathlib import Path

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
        pi_cmd="pi",
        extra_args=[],
        model=None,
        provider=None,
        session_title="pi",
        session_dir=None,
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
        pi_cmd="pi",
        extra_args=[],
        model=None,
        provider=None,
        session_title="pi",
        session_dir=None,
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


@pytest.mark.anyio
async def test_run_serializes_new_session_after_session_is_known(
    tmp_path, monkeypatch
) -> None:
    gate_path = tmp_path / "gate"
    resume_marker = tmp_path / "resume_started"

    pi_path = tmp_path / "pi"
    pi_path.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        "import os\n"
        "import sys\n"
        "import time\n"
        "\n"
        "gate = os.environ['PI_TEST_GATE']\n"
        "resume_marker = os.environ['PI_TEST_RESUME_MARKER']\n"
        "resume_value = os.environ.get('PI_TEST_RESUME_VALUE')\n"
        "\n"
        "args = sys.argv[1:]\n"
        "session_path = None\n"
        "if '--session' in args:\n"
        "    idx = args.index('--session')\n"
        "    if idx + 1 < len(args):\n"
        "        session_path = args[idx + 1]\n"
        "\n"
        "print(json.dumps({'type': 'agent_start'}), flush=True)\n"
        "\n"
        "if resume_value and session_path == resume_value:\n"
        "    with open(resume_marker, 'w', encoding='utf-8') as f:\n"
        "        f.write('started')\n"
        "        f.flush()\n"
        "    print(json.dumps({'type': 'agent_end', 'messages': []}), flush=True)\n"
        "    sys.exit(0)\n"
        "\n"
        "while not os.path.exists(gate):\n"
        "    time.sleep(0.001)\n"
        "print(json.dumps({'type': 'agent_end', 'messages': []}), flush=True)\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    pi_path.chmod(0o755)

    monkeypatch.setenv("PI_TEST_GATE", str(gate_path))
    monkeypatch.setenv("PI_TEST_RESUME_MARKER", str(resume_marker))

    runner = PiRunner(
        pi_cmd=str(pi_path),
        extra_args=[],
        model=None,
        provider=None,
        session_title="pi",
        session_dir=tmp_path / "sessions",
    )

    session_started = anyio.Event()
    resume_value: str | None = None
    new_done = anyio.Event()

    async def run_new() -> None:
        nonlocal resume_value
        async for event in runner.run("hello", None):
            if isinstance(event, StartedEvent):
                resume_value = event.resume.value
                session_started.set()
        new_done.set()

    async def run_resume() -> None:
        assert resume_value is not None
        monkeypatch.setenv("PI_TEST_RESUME_VALUE", resume_value)
        async for _event in runner.run(
            "resume", ResumeToken(engine=ENGINE, value=resume_value)
        ):
            pass

    async with anyio.create_task_group() as tg:
        tg.start_soon(run_new)
        await session_started.wait()

        tg.start_soon(run_resume)
        await anyio.sleep(0.01)

        assert not resume_marker.exists()

        gate_path.write_text("go", encoding="utf-8")
        await new_done.wait()

        with anyio.fail_after(2):
            while not resume_marker.exists():
                await anyio.sleep(0.001)
