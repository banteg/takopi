from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
import json
import logging
import sys
from typing import Any

import anyio
from anyio.abc import ByteReceiveStream
from anyio.streams.buffered import BufferedByteReceiveStream
from anyio.streams.text import TextReceiveStream
import structlog


async def iter_bytes_lines(stream: ByteReceiveStream) -> AsyncIterator[bytes]:
    buffered = BufferedByteReceiveStream(stream)
    while True:
        try:
            line = await buffered.receive_until(b"\n", sys.maxsize)
        except anyio.IncompleteRead:
            return
        yield line


async def iter_text_lines(stream: ByteReceiveStream) -> AsyncIterator[str]:
    async for line in iter_bytes_lines(stream):
        yield line.decode("utf-8", errors="replace")


@dataclass(frozen=True, slots=True)
class JsonLine:
    raw: str
    line: str
    data: dict[str, Any] | None


async def iter_jsonl(
    stream: ByteReceiveStream,
    *,
    logger,
    tag: str,
) -> AsyncIterator[JsonLine]:
    async for raw_line in iter_text_lines(stream):
        raw = raw_line.rstrip("\n")
        logger.debug("[%s][jsonl] %s", tag, raw)
        line = raw.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            logger.debug("[%s] invalid json line: %s", tag, line)
            data = None
        yield JsonLine(raw=raw, line=line, data=data)


async def drain_stderr(
    stream: ByteReceiveStream,
    logger,
    tag: str,
) -> None:
    try:
        async for line in iter_bytes_lines(stream):
            text = line.decode("utf-8", errors="replace")
            logger.debug("[%s][stderr] %s", tag, text)
    except Exception as e:
        logger.debug("[%s][stderr] drain error: %s", tag, e)
