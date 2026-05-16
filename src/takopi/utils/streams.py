from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import anyio
from anyio.abc import ByteReceiveStream

from ..logging import log_pipeline


async def iter_bytes_lines(stream: ByteReceiveStream) -> AsyncIterator[bytes]:
    buf = bytearray()
    while True:
        try:
            chunk = await stream.receive()
        except anyio.EndOfStream:
            break
        buf.extend(chunk)
        while b"\n" in buf:
            line, _, buf = buf.partition(b"\n")
            yield bytes(line)
    if buf:
        yield bytes(buf)


async def drain_stderr(
    stream: ByteReceiveStream,
    logger: Any,
    tag: str,
) -> None:
    try:
        async for line in iter_bytes_lines(stream):
            text = line.decode("utf-8", errors="replace")
            log_pipeline(
                logger,
                "subprocess.stderr",
                tag=tag,
                line=text,
            )
    except Exception as exc:  # noqa: BLE001
        log_pipeline(
            logger,
            "subprocess.stderr.error",
            tag=tag,
            error=str(exc),
        )
