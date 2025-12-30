import sys

import pytest

from takopi.runners import codex


@pytest.mark.anyio
async def test_manage_subprocess_kills_when_terminate_times_out(monkeypatch) -> None:
    import asyncio

    # Make wait_for timeout immediately to trigger kill path
    original_wait_for = asyncio.wait_for

    async def fake_wait_for(coro, timeout):
        raise asyncio.TimeoutError

    monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)

    async with codex.manage_subprocess(
        sys.executable,
        "-c",
        "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(10)",
    ) as proc:
        assert proc.returncode is None

    assert proc.returncode is not None
    assert proc.returncode != 0
