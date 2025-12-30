import asyncio

import pytest

from takopi.runners.base import ResumeToken
from takopi.runners.codex import CodexRunner


@pytest.mark.anyio
async def test_run_serializes_same_session() -> None:
    runner = CodexRunner(codex_cmd="codex", extra_args=[])
    gate = asyncio.Event()
    in_flight = 0
    max_in_flight = 0

    async def run_stub(*_args, **_kwargs):
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        await gate.wait()
        in_flight -= 1
        return (ResumeToken(engine="codex", value="sid"), "ok", True)

    runner._run = run_stub  # type: ignore[assignment]

    async def run_test() -> None:
        t1 = asyncio.create_task(runner.run("a", "codex:sid"))
        t2 = asyncio.create_task(runner.run("b", "codex:sid"))
        await asyncio.sleep(0)
        gate.set()
        await asyncio.gather(t1, t2)

    await run_test()
    assert max_in_flight == 1


@pytest.mark.anyio
async def test_run_allows_parallel_new_sessions() -> None:
    runner = CodexRunner(codex_cmd="codex", extra_args=[])
    gate = asyncio.Event()
    in_flight = 0
    max_in_flight = 0

    async def run_stub(*_args, **_kwargs):
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        await gate.wait()
        in_flight -= 1
        return (ResumeToken(engine="codex", value="sid"), "ok", True)

    runner._run = run_stub  # type: ignore[assignment]

    async def run_test() -> None:
        t1 = asyncio.create_task(runner.run("a", None))
        t2 = asyncio.create_task(runner.run("b", None))
        await asyncio.sleep(0.01)
        gate.set()
        await asyncio.gather(t1, t2)

    await run_test()
    assert max_in_flight == 2
