import anyio
import pytest

from takopi.utils.streams import iter_bytes_lines


@pytest.mark.anyio
async def test_iter_bytes_lines_with_newlines() -> None:
    async def stream_producer(send_stream):
        await send_stream.send(b"line1\nline2\nline3\n")
        await send_stream.aclose()

    send_stream, receive_stream = anyio.create_memory_object_stream()
    lines = []
    async with anyio.create_task_group() as tg:
        tg.start_soon(stream_producer, send_stream)
        async for line in iter_bytes_lines(receive_stream):  # type: ignore[arg-type]
            lines.append(line)

    assert lines == [b"line1", b"line2", b"line3"]


@pytest.mark.anyio
async def test_iter_bytes_lines_partial_lines() -> None:
    async def stream_producer(send_stream):
        await send_stream.send(b"line1\npartial")
        await send_stream.send(b" line\n")
        await send_stream.aclose()

    send_stream, receive_stream = anyio.create_memory_object_stream()
    lines = []
    async with anyio.create_task_group() as tg:
        tg.start_soon(stream_producer, send_stream)
        async for line in iter_bytes_lines(receive_stream):  # type: ignore[arg-type]
            lines.append(line)

    assert lines == [b"line1", b"partial line"]


@pytest.mark.anyio
async def test_iter_bytes_lines_buffer_at_end() -> None:
    async def stream_producer(send_stream):
        await send_stream.send(b"line1\n")
        await send_stream.send(b"partial")
        await send_stream.aclose()

    send_stream, receive_stream = anyio.create_memory_object_stream()
    lines = []
    async with anyio.create_task_group() as tg:
        tg.start_soon(stream_producer, send_stream)
        async for line in iter_bytes_lines(receive_stream):  # type: ignore[arg-type]
            lines.append(line)

    # Note: iter_bytes_lines only returns complete lines (with newline)
    # partial data without newline is not returned
    assert lines == [b"line1"]
