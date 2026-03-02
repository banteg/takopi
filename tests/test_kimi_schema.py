from __future__ import annotations

from pathlib import Path

import pytest

from takopi.schemas import kimi as kimi_schema


def _fixture_path(name: str) -> Path:
    return Path(__file__).parent / "fixtures" / name


def _decode_fixture(name: str) -> list[str]:
    path = _fixture_path(name)
    errors: list[str] = []

    for lineno, line in enumerate(path.read_bytes().splitlines(), 1):
        if not line.strip():
            continue
        try:
            decoded = kimi_schema.decode_stream_json_line(line)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"line {lineno}: {exc.__class__.__name__}: {exc}")
            continue

        _ = decoded

    return errors


@pytest.mark.parametrize(
    "fixture",
    [
        "kimi_stream_json_session.jsonl",
    ],
)
def test_kimi_schema_parses_fixture(fixture: str) -> None:
    errors = _decode_fixture(fixture)

    assert not errors, f"{fixture} had {len(errors)} errors: " + "; ".join(errors[:5])


def test_decode_assistant_message_with_tool_calls() -> None:
    line = b'{"role":"assistant","content":"Let me check.","tool_calls":[{"type":"function","id":"tc_1","function":{"name":"Shell","arguments":"{\\"command\\":\\"ls\\"}"}}]}'
    event = kimi_schema.decode_stream_json_line(line)

    assert isinstance(event, kimi_schema.AssistantMessage)
    assert event.content == "Let me check."
    assert event.tool_calls is not None
    assert len(event.tool_calls) == 1
    assert event.tool_calls[0].id == "tc_1"
    assert event.tool_calls[0].function.name == "Shell"
    assert event.tool_calls[0].function.arguments == '{"command":"ls"}'


def test_decode_assistant_message_without_tool_calls() -> None:
    line = b'{"role":"assistant","content":"Hello!"}'
    event = kimi_schema.decode_stream_json_line(line)

    assert isinstance(event, kimi_schema.AssistantMessage)
    assert event.content == "Hello!"
    assert event.tool_calls is None


def test_decode_tool_message() -> None:
    line = b'{"role":"tool","tool_call_id":"tc_1","content":"file1.txt\\nfile2.txt"}'
    event = kimi_schema.decode_stream_json_line(line)

    assert isinstance(event, kimi_schema.ToolMessage)
    assert event.tool_call_id == "tc_1"
    assert event.content == "file1.txt\nfile2.txt"


def test_decode_user_message() -> None:
    line = b'{"role":"user","content":"List files please"}'
    event = kimi_schema.decode_stream_json_line(line)

    assert isinstance(event, kimi_schema.UserMessage)
    assert event.content == "List files please"


def test_decode_system_message() -> None:
    line = b'{"role":"system","content":"You are a helpful assistant."}'
    event = kimi_schema.decode_stream_json_line(line)

    assert isinstance(event, kimi_schema.SystemMessage)
    assert event.content == "You are a helpful assistant."
