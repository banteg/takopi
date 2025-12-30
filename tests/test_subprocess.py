import sys

import pytest

from takopi import exec_bridge


@pytest.mark.anyio
async def test_manage_subprocess_kills_when_terminate_times_out() -> None:
    async with exec_bridge.manage_subprocess(
        sys.executable,
        "-c",
        "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(10)",
        terminate_timeout=0.01,
    ) as proc:
        assert proc.returncode is None

    assert proc.returncode is not None
    assert proc.returncode != 0
