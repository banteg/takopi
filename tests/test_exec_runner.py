import anyio

import pytest

from takopi.runners.base import ResumeToken, RunResult
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
        tg.start_soon(runner.run, "a", token)
        tg.start_soon(runner.run, "b", token)
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
        tg.start_soon(runner.run, "a", None)
        tg.start_soon(runner.run, "b", None)
        await anyio.sleep(0.01)
        gate.set()
    assert max_in_flight == 2
