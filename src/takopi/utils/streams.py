from __future__ import annotations

from collections import deque
from collections.abc import AsyncIterator
import logging

import anyio
from anyio.abc import ByteReceiveStream
from anyio.streams.text import TextReceiveStream


async def iter_text_lines(stream: ByteReceiveStream) -> AsyncIterator[str]:
    text_stream = TextReceiveStream(stream, errors="replace")
    buffer = ""
    while True:
        try:
            chunk = await text_stream.receive()
        except anyio.EndOfStream:
            if buffer:
                yield buffer
            return
        buffer += chunk
        while True:
            split_at = buffer.find("\n")
            if split_at < 0:
                break
            line = buffer[: split_at + 1]
            buffer = buffer[split_at + 1 :]
            yield line


async def iter_bytes_lines(stream: ByteReceiveStream) -> AsyncIterator[bytes]:
    buffer = bytearray()
    while True:
        try:
            chunk = await stream.receive()
        except anyio.EndOfStream:
            if buffer:
                yield bytes(buffer)
            return
        if not chunk:
            continue
        buffer.extend(chunk)
        while True:
            split_at = buffer.find(b"\n")
            if split_at < 0:
                break
            line = bytes(buffer[: split_at + 1])
            del buffer[: split_at + 1]
            yield line


async def drain_stderr(
    stream: ByteReceiveStream,
    chunks: deque[str],
    logger: logging.Logger,
    tag: str,
) -> None:
    try:
        async for line in iter_text_lines(stream):
            logger.debug("[%s][stderr] %s", tag, line.rstrip())
            chunks.append(line)
    except Exception as e:
        logger.debug("[%s][stderr] drain error: %s", tag, e)
