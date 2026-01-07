import json

from takopi.events import EventFactory
from takopi.model import ActionEvent
from takopi.runners.codex import translate_codex_event
from takopi.schemas import codex as codex_schema


def _decode_event(payload: dict) -> codex_schema.ThreadEvent:
    return codex_schema.decode_event(json.dumps(payload))


def _translate_event(payload: dict) -> list:
    return translate_codex_event(
        _decode_event(payload),
        title="Codex",
        factory=EventFactory("codex"),
    )


def test_translate_mcp_tool_call_summarizes_structured_content() -> None:
    evt = {
        "type": "item.completed",
        "item": {
            "id": "item_1",
            "type": "mcp_tool_call",
            "server": "docs",
            "tool": "search",
            "arguments": {"q": "hi"},
            "result": {
                "content": [{"type": "text", "text": "ok"}],
                "structured_content": {"matches": 3},
            },
            "error": None,
            "status": "completed",
        },
    }

    out = _translate_event(evt)
    assert len(out) == 1
    assert isinstance(out[0], ActionEvent)
    summary = out[0].action.detail["result_summary"]
    assert summary["content_blocks"] == 1
    assert summary["has_structured"] is True


def test_translate_mcp_tool_call_summarizes_null_structured_content() -> None:
    evt = {
        "type": "item.completed",
        "item": {
            "id": "item_2",
            "type": "mcp_tool_call",
            "server": "docs",
            "tool": "search",
            "arguments": None,
            "result": {"content": [], "structured_content": None},
            "error": None,
            "status": "completed",
        },
    }

    out = _translate_event(evt)
    assert len(out) == 1
    assert isinstance(out[0], ActionEvent)
    assert out[0].action.detail["result_summary"]["has_structured"] is False


def test_translate_mcp_tool_call_missing_error_is_ok() -> None:
    evt = {
        "type": "item.completed",
        "item": {
            "id": "item_4",
            "type": "mcp_tool_call",
            "server": "docs",
            "tool": "search",
            "arguments": None,
            "status": "completed",
            "result": {"content": [], "structured_content": None},
            "error": None,
        },
    }

    out = _translate_event(evt)
    assert len(out) == 1
    assert isinstance(out[0], ActionEvent)
    assert out[0].ok is True


def test_translate_command_execution_allows_null_exit_code() -> None:
    evt = {
        "type": "item.completed",
        "item": {
            "id": "item_5",
            "type": "command_execution",
            "command": "ls -la",
            "aggregated_output": "",
            "exit_code": None,
            "status": "completed",
        },
    }

    out = _translate_event(evt)
    assert len(out) == 1
    assert isinstance(out[0], ActionEvent)
    assert out[0].ok is True
    assert out[0].action.detail["exit_code"] is None


def test_translate_file_change_normalizes_changes() -> None:
    evt = {
        "type": "item.completed",
        "item": {
            "id": "item_6",
            "type": "file_change",
            "changes": [{"path": "README.md", "kind": "update"}],
            "status": "completed",
        },
    }

    out = _translate_event(evt)
    assert len(out) == 1
    assert isinstance(out[0], ActionEvent)
    changes = out[0].action.detail["changes"]
    assert changes == [{"path": "README.md", "kind": "update"}]
