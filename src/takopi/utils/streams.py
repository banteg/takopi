from __future__ import annotations

from collections.abc import AsyncIterator
import logging
import sys

import anyio
from anyio.abc import ByteReceiveStream
from anyio.streams.buffered import BufferedByteReceiveStream


async def iter_bytes_lines(stream: ByteReceiveStream) -> AsyncIterator[bytes]:
    buffered = BufferedByteReceiveStream(stream)
    while True:
        try:
            line = await buffered.receive_until(b"\n", sys.maxsize)
        except anyio.IncompleteRead:
            return
        yield line


async def drain_stderr(
    stream: ByteReceiveStream,
    logger: logging.Logger,
    tag: str,
) -> None:
    try:
        async for line in iter_bytes_lines(stream):
            text = line.decode("utf-8", errors="replace")
            logger.debug("[%s][stderr] %s", tag, text)
    except Exception as e:
        logger.debug("[%s][stderr] drain error: %s", tag, e)
