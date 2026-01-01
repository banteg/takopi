from __future__ import annotations

from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass
import json
import logging
from typing import Any

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


@dataclass(frozen=True, slots=True)
class JsonLine:
    raw: str
    line: str
    data: dict[str, Any] | None


async def iter_jsonl(
    stream: ByteReceiveStream,
    *,
    logger: logging.Logger,
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
