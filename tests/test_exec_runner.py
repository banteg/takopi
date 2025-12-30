import anyio
import pytest

from takopi.exec_bridge import CodexExecRunner, EventCallback


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


@pytest.mark.anyio
async def test_run_serialized_allows_parallel_new_sessions() -> None:
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
        tg.start_soon(runner.run_serialized, "a", None)
        tg.start_soon(runner.run_serialized, "b", None)
        with anyio.move_on_after(1):
            while max_in_flight < 2:
                await anyio.sleep(0)
        gate.set()

    assert max_in_flight == 2


@pytest.mark.anyio
async def test_new_session_holds_lock_for_resumes() -> None:
    runner = CodexExecRunner(codex_cmd="codex", extra_args=[])
    finish = anyio.Event()
    resume_started = anyio.Event()

    async def run_stub(
        _prompt: str,
        session_id: str | None,
        on_event: EventCallback | None = None,
    ) -> tuple[str, str, bool]:
        if session_id is None:
            if on_event:
                await on_event({"type": "thread.started", "thread_id": "sid"})
            await finish.wait()
            return ("sid", "ok", True)
        resume_started.set()
        return ("sid", "ok", True)

    runner.run = run_stub  # type: ignore[assignment]

    async with anyio.create_task_group() as tg:
        tg.start_soon(runner.run_serialized, "first", None)
        await anyio.sleep(0)
        tg.start_soon(runner.run_serialized, "resume", "sid")
        await anyio.sleep(0)
        assert not resume_started.is_set()
        finish.set()
