from __future__ import annotations

from collections.abc import AsyncIterator

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
