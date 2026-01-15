import json
from pathlib import Path
from typing import cast

import anyio
import pytest

import takopi.runners.claude as claude_runner
from takopi.model import ActionEvent, CompletedEvent, ResumeToken, StartedEvent
from takopi.runners.claude import (
    ClaudeRunner,
    ClaudeStreamState,
    ENGINE,
    translate_claude_event,
)
from takopi.schemas import claude as claude_schema


def _load_fixture(
    name: str, *, session_id: str | None = None
) -> list[claude_schema.StreamJsonMessage]:
    path = Path(__file__).parent / "fixtures" / name
    events = [
        claude_schema.decode_stream_json_line(line)
        for line in path.read_bytes().splitlines()
        if line.strip()
    ]
    if session_id is None:
        return events
    return [
        event for event in events if getattr(event, "session_id", None) == session_id
    ]


def _decode_event(payload: dict) -> claude_schema.StreamJsonMessage:
    data_payload = dict(payload)
    data_payload.setdefault("uuid", "uuid")
    data_payload.setdefault("session_id", "session")
    match data_payload.get("type"):
        case "assistant":
            message = dict(data_payload.get("message", {}))
            message.setdefault("role", "assistant")
            message.setdefault("content", [])
            message.setdefault("model", "claude")
            data_payload["message"] = message
        case "user":
            message = dict(data_payload.get("message", {}))
            message.setdefault("role", "user")
            message.setdefault("content", [])
            data_payload["message"] = message
    data = json.dumps(data_payload).encode("utf-8")
    return claude_schema.decode_stream_json_line(data)


def test_claude_resume_format_and_extract() -> None:
    runner = ClaudeRunner(claude_cmd="claude")
    token = ResumeToken(engine=ENGINE, value="sid")

    assert runner.format_resume(token) == "`claude --resume sid`"
    assert runner.extract_resume("`claude --resume sid`") == token
    assert runner.extract_resume("claude -r other") == ResumeToken(
        engine=ENGINE, value="other"
    )
    assert runner.extract_resume("`codex resume sid`") is None


def test_build_runner_uses_shutil_which(monkeypatch) -> None:
    expected = r"C:\Tools\claude.cmd"
    called: dict[str, str] = {}

    def fake_which(name: str) -> str | None:
        called["name"] = name
        return expected

    monkeypatch.setattr(claude_runner.shutil, "which", fake_which)
    runner = cast(ClaudeRunner, claude_runner.build_runner({}, Path("takopi.toml")))

    assert called["name"] == "claude"
    assert runner.claude_cmd == expected


def test_translate_success_fixture() -> None:
    state = ClaudeStreamState()
    events: list = []
    for event in _load_fixture(
        "claude_stream_json_session.jsonl",
        session_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    ):
        events.extend(
            translate_claude_event(
                event,
                title="claude",
                state=state,
                factory=state.factory,
            )
        )

    assert isinstance(events[0], StartedEvent)
    started = next(evt for evt in events if isinstance(evt, StartedEvent))

    action_events = [evt for evt in events if isinstance(evt, ActionEvent)]
    assert len(action_events) == 4

    started_actions = {
        (evt.action.id, evt.phase): evt
        for evt in action_events
        if evt.phase == "started"
    }
    assert (
        started_actions[("toolu_01BASH_LS_EXAMPLE", "started")].action.kind == "command"
    )
    write_action = started_actions[("toolu_02", "started")].action
    assert write_action.kind == "file_change"
    assert write_action.detail["changes"][0]["path"] == "notes.md"

    completed_actions = {
        (evt.action.id, evt.phase): evt
        for evt in action_events
        if evt.phase == "completed"
    }
    assert completed_actions[("toolu_01BASH_LS_EXAMPLE", "completed")].ok is True
    assert completed_actions[("toolu_02", "completed")].ok is True

    completed = next(evt for evt in events if isinstance(evt, CompletedEvent))
    assert events[-1] == completed
    assert completed.ok is True
    assert completed.resume == started.resume
    assert completed.answer == "I see README.md, pyproject.toml, and src/."


def test_translate_error_fixture_permission_denials() -> None:
    state = ClaudeStreamState()
    events: list = []
    for event in _load_fixture(
        "claude_stream_json_session.jsonl",
        session_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
    ):
        events.extend(
            translate_claude_event(
                event,
                title="claude",
                state=state,
                factory=state.factory,
            )
        )

    started = next(evt for evt in events if isinstance(evt, StartedEvent))
    completed = next(evt for evt in events if isinstance(evt, CompletedEvent))
    assert completed.ok is False
    assert completed.error is not None
    assert "claude run failed" in completed.error
    assert completed.resume == started.resume


def test_tool_results_pop_pending_actions() -> None:
    state = ClaudeStreamState()

    tool_use_event = {
        "type": "assistant",
        "message": {
            "id": "msg_1",
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "Bash",
                    "input": {"command": "echo hi"},
                }
            ],
        },
    }
    tool_result_event = {
        "type": "user",
        "message": {
            "id": "msg_2",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_1",
                    "content": "ok",
                    "is_error": False,
                }
            ],
        },
    }

    translate_claude_event(
        _decode_event(tool_use_event),
        title="claude",
        state=state,
        factory=state.factory,
    )
    assert "toolu_1" in state.pending_actions

    translate_claude_event(
        _decode_event(tool_result_event),
        title="claude",
        state=state,
        factory=state.factory,
    )
    assert not state.pending_actions


def test_translate_thinking_block() -> None:
    state = ClaudeStreamState()
    event = {
        "type": "assistant",
        "message": {
            "id": "msg_1",
            "content": [
                {
                    "type": "thinking",
                    "thinking": "Consider the options.",
                    "signature": "sig",
                }
            ],
        },
    }

    events = translate_claude_event(
        _decode_event(event),
        title="claude",
        state=state,
        factory=state.factory,
    )

    assert len(events) == 1
    assert isinstance(events[0], ActionEvent)
    assert events[0].phase == "completed"
    assert events[0].action.kind == "note"
    assert events[0].action.title == "Consider the options."
    assert events[0].ok is True


@pytest.mark.anyio
async def test_run_serializes_same_session() -> None:
    runner = ClaudeRunner(claude_cmd="claude")
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
                resume=ResumeToken(engine=ENGINE, value="sid"),
                ok=True,
                answer="ok",
            )
        finally:
            in_flight -= 1

    runner.run_impl = run_stub  # type: ignore[assignment]

    async def drain(prompt: str, resume: ResumeToken | None) -> None:
        async for _event in runner.run(prompt, resume):
            pass

    token = ResumeToken(engine=ENGINE, value="sid")
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
    session_id = "session_01"

    claude_path = tmp_path / "claude"
    claude_path.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        "import os\n"
        "import sys\n"
        "import time\n"
        "\n"
        "gate = os.environ['CLAUDE_TEST_GATE']\n"
        "resume_marker = os.environ['CLAUDE_TEST_RESUME_MARKER']\n"
        "session_id = os.environ['CLAUDE_TEST_SESSION_ID']\n"
        "\n"
        "init = {\n"
        "    'type': 'system',\n"
        "    'subtype': 'init',\n"
        "    'uuid': 'uuid',\n"
        "    'session_id': session_id,\n"
        "    'apiKeySource': 'env',\n"
        "    'cwd': '.',\n"
        "    'tools': [],\n"
        "    'mcp_servers': [],\n"
        "    'model': 'claude',\n"
        "    'permissionMode': 'default',\n"
        "    'slash_commands': [],\n"
        "    'output_style': 'default',\n"
        "}\n"
        "\n"
        "args = sys.argv[1:]\n"
        "if '--resume' in args or '-r' in args:\n"
        "    print(json.dumps(init), flush=True)\n"
        "    with open(resume_marker, 'w', encoding='utf-8') as f:\n"
        "        f.write('started')\n"
        "        f.flush()\n"
        "    sys.exit(0)\n"
        "\n"
        "print(json.dumps(init), flush=True)\n"
        "while not os.path.exists(gate):\n"
        "    time.sleep(0.001)\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    claude_path.chmod(0o755)

    monkeypatch.setenv("CLAUDE_TEST_GATE", str(gate_path))
    monkeypatch.setenv("CLAUDE_TEST_RESUME_MARKER", str(resume_marker))
    monkeypatch.setenv("CLAUDE_TEST_SESSION_ID", session_id)

    runner = ClaudeRunner(claude_cmd=str(claude_path))

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


@pytest.mark.anyio
async def test_run_strips_anthropic_api_key_by_default(tmp_path, monkeypatch) -> None:
    claude_path = tmp_path / "claude"
    claude_path.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        "import os\n"
        "\n"
        "session_id = 'session_01'\n"
        "status = 'set' if os.environ.get('ANTHROPIC_API_KEY') else 'unset'\n"
        "init = {\n"
        "    'type': 'system',\n"
        "    'subtype': 'init',\n"
        "    'uuid': 'uuid',\n"
        "    'session_id': session_id,\n"
        "    'apiKeySource': 'env',\n"
        "    'cwd': '.',\n"
        "    'tools': [],\n"
        "    'mcp_servers': [],\n"
        "    'model': 'claude',\n"
        "    'permissionMode': 'default',\n"
        "    'slash_commands': [],\n"
        "    'output_style': 'default',\n"
        "}\n"
        "print(json.dumps(init), flush=True)\n"
        "result = {\n"
        "    'type': 'result',\n"
        "    'subtype': 'success',\n"
        "    'uuid': 'uuid',\n"
        "    'session_id': session_id,\n"
        "    'duration_ms': 0,\n"
        "    'duration_api_ms': 0,\n"
        "    'is_error': False,\n"
        "    'num_turns': 1,\n"
        "    'result': f'api={status}',\n"
        "    'total_cost_usd': 0.0,\n"
        "    'usage': {'input_tokens': 0, 'output_tokens': 0},\n"
        "    'modelUsage': {},\n"
        "    'permission_denials': [],\n"
        "}\n"
        "print(json.dumps(result), flush=True)\n"
        "raise SystemExit(0)\n",
        encoding="utf-8",
    )
    claude_path.chmod(0o755)

    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret")

    runner = ClaudeRunner(claude_cmd=str(claude_path))
    answer: str | None = None
    async for event in runner.run("hello", None):
        if isinstance(event, CompletedEvent):
            answer = event.answer
    assert answer == "api=unset"

    runner_api = ClaudeRunner(claude_cmd=str(claude_path), use_api_billing=True)
    answer = None
    async for event in runner_api.run("hello", None):
        if isinstance(event, CompletedEvent):
            answer = event.answer
    assert answer == "api=set"
