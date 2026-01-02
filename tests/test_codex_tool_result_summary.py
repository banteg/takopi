from takopi.model import ActionEvent
from takopi.runners.codex import translate_codex_event


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

    out = translate_codex_event(evt, title="Codex")
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
            "result": {"content": [], "structured_content": None},
            "error": None,
            "status": "completed",
        },
    }

    out = translate_codex_event(evt, title="Codex")
    assert len(out) == 1
    assert isinstance(out[0], ActionEvent)
    assert out[0].action.detail["result_summary"]["has_structured"] is False


def test_translate_mcp_tool_call_summarizes_legacy_structured_key() -> None:
    evt = {
        "type": "item.completed",
        "item": {
            "id": "item_3",
            "type": "mcp_tool_call",
            "server": "docs",
            "tool": "search",
            "result": {"structured": {"matches": 3}},
            "error": None,
            "status": "completed",
        },
    }

    out = translate_codex_event(evt, title="Codex")
    assert len(out) == 1
    assert isinstance(out[0], ActionEvent)
    assert out[0].action.detail["result_summary"]["has_structured"] is True


def test_translate_mcp_tool_call_missing_error_is_ok() -> None:
    evt = {
        "type": "item.completed",
        "item": {
            "id": "item_4",
            "type": "mcp_tool_call",
            "server": "docs",
            "tool": "search",
            "status": "completed",
            "result": {"content": []},
        },
    }

    out = translate_codex_event(evt, title="Codex")
    assert len(out) == 1
    assert isinstance(out[0], ActionEvent)
    assert out[0].ok is True


def test_translate_command_execution_allows_missing_exit_code() -> None:
    evt = {
        "type": "item.completed",
        "item": {
            "id": "item_5",
            "type": "command_execution",
            "command": "ls -la",
            "aggregated_output": "",
            "status": "completed",
        },
    }

    out = translate_codex_event(evt, title="Codex")
    assert len(out) == 1
    assert isinstance(out[0], ActionEvent)
    assert out[0].ok is True
    assert out[0].action.detail["exit_code"] is None
