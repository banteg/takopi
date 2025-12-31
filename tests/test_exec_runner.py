import anyio

import pytest

from takopi.model import ResumeToken, RunResult
from takopi.runner import NO_OP_SINK
from takopi.runners.codex import CodexRunner


@pytest.mark.anyio
async def test_run_serializes_same_session() -> None:
    runner = CodexRunner(codex_cmd="codex", extra_args=[])
    gate = anyio.Event()
    in_flight = 0
    max_in_flight = 0

    async def run_stub(*_args, **_kwargs):
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        await gate.wait()
        in_flight -= 1
        return RunResult(
            resume=ResumeToken(engine="codex", value="sid"),
            answer="ok",
        )

    runner._run = run_stub  # type: ignore[assignment]

    token = ResumeToken(engine="codex", value="sid")
    async with anyio.create_task_group() as tg:
        tg.start_soon(runner.run, "a", token, NO_OP_SINK)
        tg.start_soon(runner.run, "b", token, NO_OP_SINK)
        await anyio.sleep(0)
        gate.set()
    assert max_in_flight == 1


@pytest.mark.anyio
async def test_run_allows_parallel_new_sessions() -> None:
    runner = CodexRunner(codex_cmd="codex", extra_args=[])
    gate = anyio.Event()
    in_flight = 0
    max_in_flight = 0

    async def run_stub(*_args, **_kwargs):
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        await gate.wait()
        in_flight -= 1
        return RunResult(
            resume=ResumeToken(engine="codex", value="sid"),
            answer="ok",
        )

    runner._run = run_stub  # type: ignore[assignment]

    async with anyio.create_task_group() as tg:
        tg.start_soon(runner.run, "a", None, NO_OP_SINK)
        tg.start_soon(runner.run, "b", None, NO_OP_SINK)
        await anyio.sleep(0.01)
        gate.set()
    assert max_in_flight == 2


@pytest.mark.anyio
async def test_run_allows_parallel_different_sessions() -> None:
    runner = CodexRunner(codex_cmd="codex", extra_args=[])
    gate = anyio.Event()
    in_flight = 0
    max_in_flight = 0

    async def run_stub(*_args, **_kwargs):
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        await gate.wait()
        in_flight -= 1
        return RunResult(
            resume=ResumeToken(engine="codex", value="sid"),
            answer="ok",
        )

    runner._run = run_stub  # type: ignore[assignment]

    token_a = ResumeToken(engine="codex", value="sid-a")
    token_b = ResumeToken(engine="codex", value="sid-b")
    async with anyio.create_task_group() as tg:
        tg.start_soon(runner.run, "a", token_a, NO_OP_SINK)
        tg.start_soon(runner.run, "b", token_b, NO_OP_SINK)
        await anyio.sleep(0)
        gate.set()
    assert max_in_flight == 2


@pytest.mark.anyio
async def test_run_serializes_new_session_after_session_is_known(
    tmp_path, monkeypatch
) -> None:
    gate_path = tmp_path / "gate"
    resume_marker = tmp_path / "resume_started"
    thread_id = "019b73c4-0c3f-7701-a0bb-aac6b4d8a3bc"

    codex_path = tmp_path / "codex"
    codex_path.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        "import os\n"
        "import sys\n"
        "import time\n"
        "\n"
        "gate = os.environ['CODEX_TEST_GATE']\n"
        "resume_marker = os.environ['CODEX_TEST_RESUME_MARKER']\n"
        "thread_id = os.environ['CODEX_TEST_THREAD_ID']\n"
        "\n"
        "args = sys.argv[1:]\n"
        "if 'resume' in args:\n"
        "    with open(resume_marker, 'w', encoding='utf-8') as f:\n"
        "        f.write('started')\n"
        "        f.flush()\n"
        "    sys.exit(0)\n"
        "\n"
        "print(json.dumps({'type': 'thread.started', 'thread_id': thread_id}), flush=True)\n"
        "while not os.path.exists(gate):\n"
        "    time.sleep(0.01)\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    codex_path.chmod(0o755)

    monkeypatch.setenv("CODEX_TEST_GATE", str(gate_path))
    monkeypatch.setenv("CODEX_TEST_RESUME_MARKER", str(resume_marker))
    monkeypatch.setenv("CODEX_TEST_THREAD_ID", thread_id)

    runner = CodexRunner(codex_cmd=str(codex_path), extra_args=[])

    session_started = anyio.Event()
    resume_value: str | None = None

    async def on_event(event) -> None:
        nonlocal resume_value
        if event.get("type") == "session.started":
            resume_value = event["resume"].value
            session_started.set()

    new_done = anyio.Event()

    async def run_new() -> None:
        await runner.run("hello", None, on_event)
        new_done.set()

    async def run_resume() -> None:
        assert resume_value is not None
        await runner.run(
            "resume", ResumeToken(engine="codex", value=resume_value), NO_OP_SINK
        )

    async with anyio.create_task_group() as tg:
        tg.start_soon(run_new)
        await session_started.wait()

        tg.start_soon(run_resume)
        await anyio.sleep(0.05)

        assert not resume_marker.exists()

        gate_path.write_text("go", encoding="utf-8")
        await new_done.wait()

        with anyio.fail_after(2):
            while not resume_marker.exists():
                await anyio.sleep(0.01)


@pytest.mark.anyio
async def test_run_serializes_two_new_sessions_same_thread(tmp_path, monkeypatch) -> None:
    gate_path = tmp_path / "gate"
    thread_id = "019b73c4-0c3f-7701-a0bb-aac6b4d8a3bc"

    codex_path = tmp_path / "codex"
    codex_path.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        "import os\n"
        "import sys\n"
        "import time\n"
        "\n"
        "gate = os.environ['CODEX_TEST_GATE']\n"
        "thread_id = os.environ['CODEX_TEST_THREAD_ID']\n"
        "\n"
        "print(json.dumps({'type': 'thread.started', 'thread_id': thread_id}), flush=True)\n"
        "while not os.path.exists(gate):\n"
        "    time.sleep(0.01)\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    codex_path.chmod(0o755)

    monkeypatch.setenv("CODEX_TEST_GATE", str(gate_path))
    monkeypatch.setenv("CODEX_TEST_THREAD_ID", thread_id)

    runner = CodexRunner(codex_cmd=str(codex_path), extra_args=[])

    started_first = anyio.Event()
    started_second = anyio.Event()

    async def on_event_first(event) -> None:
        if event.get("type") == "session.started":
            started_first.set()

    async def on_event_second(event) -> None:
        if event.get("type") == "session.started":
            started_second.set()

    async def run_first() -> None:
        await runner.run("one", None, on_event_first)

    async def run_second() -> None:
        await runner.run("two", None, on_event_second)

    async with anyio.create_task_group() as tg:
        tg.start_soon(run_first)
        tg.start_soon(run_second)

        with anyio.fail_after(2):
            while not (started_first.is_set() or started_second.is_set()):
                await anyio.sleep(0.01)

        assert not (started_first.is_set() and started_second.is_set())

        gate_path.write_text("go", encoding="utf-8")

        with anyio.fail_after(2):
            await started_first.wait()
            await started_second.wait()
