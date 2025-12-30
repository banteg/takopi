import anyio
import pytest

from takopi.exec_bridge import CodexExecRunner


@pytest.mark.anyio
async def test_run_serialized_serializes_same_session() -> None:
    runner = CodexExecRunner(codex_cmd="codex", extra_args=[])
    gate = anyio.Event()
    in_flight = 0
    max_in_flight = 0

    async def run_stub(*_args, **_kwargs):
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        await gate.wait()
        in_flight -= 1
        return ("sid", "ok", True)

    runner.run = run_stub  # type: ignore[assignment]

    async with anyio.create_task_group() as tg:
        tg.start_soon(runner.run_serialized, "a", "sid")
        tg.start_soon(runner.run_serialized, "b", "sid")
        await anyio.sleep(0)
        gate.set()

    assert max_in_flight == 1
