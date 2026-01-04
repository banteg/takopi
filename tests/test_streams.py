import logging

import anyio
import pytest

from takopi.utils.streams import iter_text_lines, iter_jsonl


@pytest.mark.anyio
async def test_iter_text_lines_with_newlines() -> None:
    async def stream_producer(send_stream):
        await send_stream.send(b"line1\nline2\nline3\n")
        await send_stream.aclose()

    send_stream, receive_stream = anyio.create_memory_object_stream()
    lines = []
    async with anyio.create_task_group() as tg:
        tg.start_soon(stream_producer, send_stream)
        async for line in iter_text_lines(receive_stream):  # type: ignore[arg-type]
            lines.append(line)

    assert lines == ["line1\n", "line2\n", "line3\n"]


@pytest.mark.anyio
async def test_iter_text_lines_partial_lines() -> None:
    async def stream_producer(send_stream):
        await send_stream.send(b"line1\npartial")
        await send_stream.send(b" line\n")
        await send_stream.aclose()

    send_stream, receive_stream = anyio.create_memory_object_stream()
    lines = []
    async with anyio.create_task_group() as tg:
        tg.start_soon(stream_producer, send_stream)
        async for line in iter_text_lines(receive_stream):  # type: ignore[arg-type]
            lines.append(line)

    assert lines == ["line1\n", "partial line\n"]


@pytest.mark.anyio
async def test_iter_text_lines_buffer_at_end() -> None:
    async def stream_producer(send_stream):
        await send_stream.send(b"line1\n")
        await send_stream.send(b"partial")
        await send_stream.aclose()

    send_stream, receive_stream = anyio.create_memory_object_stream()
    lines = []
    async with anyio.create_task_group() as tg:
        tg.start_soon(stream_producer, send_stream)
        async for line in iter_text_lines(receive_stream):  # type: ignore[arg-type]
            lines.append(line)

    assert lines == ["line1\n", "partial"]


@pytest.mark.anyio
async def test_iter_jsonl_valid_json() -> None:
    async def stream_producer(send_stream):
        await send_stream.send(b'{"key": "value1"}\n')
        await send_stream.send(b'{"key": "value2"}\n')
        await send_stream.aclose()

    logger = logging.getLogger(__name__)

    send_stream, receive_stream = anyio.create_memory_object_stream()
    lines = []
    async with anyio.create_task_group() as tg:
        tg.start_soon(stream_producer, send_stream)
        async for json_line in iter_jsonl(receive_stream, logger=logger, tag="test"):  # type: ignore[arg-type]
            lines.append(json_line)

    assert len(lines) == 2
    assert lines[0].data == {"key": "value1"}
    assert lines[1].data == {"key": "value2"}


@pytest.mark.anyio
async def test_iter_jsonl_invalid_json() -> None:
    async def stream_producer(send_stream):
        await send_stream.send(b"invalid json\n")
        await send_stream.send(b'{"key": "value"}\n')
        await send_stream.aclose()

    logger = logging.getLogger(__name__)

    send_stream, receive_stream = anyio.create_memory_object_stream()
    lines = []
    async with anyio.create_task_group() as tg:
        tg.start_soon(stream_producer, send_stream)
        async for json_line in iter_jsonl(receive_stream, logger=logger, tag="test"):  # type: ignore[arg-type]
            lines.append(json_line)

    assert len(lines) == 2
    assert lines[0].data is None
    assert lines[1].data == {"key": "value"}


@pytest.mark.anyio
async def test_iter_jsonl_empty_lines() -> None:
    async def stream_producer(send_stream):
        await send_stream.send(b"\n")
        await send_stream.send(b"  \n")
        await send_stream.send(b'{"key": "value"}\n')
        await send_stream.aclose()

    logger = logging.getLogger(__name__)

    send_stream, receive_stream = anyio.create_memory_object_stream()
    lines = []
    async with anyio.create_task_group() as tg:
        tg.start_soon(stream_producer, send_stream)
        async for json_line in iter_jsonl(receive_stream, logger=logger, tag="test"):  # type: ignore[arg-type]
            lines.append(json_line)

    assert len(lines) == 1
    assert lines[0].data == {"key": "value"}
