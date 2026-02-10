from __future__ import annotations

from pathlib import Path

import pytest

from takopi.backends import EngineConfig
from takopi.config import ConfigError
from takopi.events import EventFactory
from takopi.model import ActionEvent, CompletedEvent, StartedEvent
from takopi.runners.codex import (
    _AgentMessageSummary,
    CodexRunner,
    _format_change_summary,
    _normalize_change_list,
    _parse_reconnect_message,
    _select_final_answer,
    _short_tool_name,
    _summarize_todo_list,
    _summarize_tool_result,
    _todo_title,
    build_runner,
    find_exec_only_flag,
    translate_codex_event,
)
from takopi.schemas import codex as codex_schema


def test_codex_helper_functions() -> None:
    assert find_exec_only_flag(["--json"]) == "--json"
    assert find_exec_only_flag(["--output-schema=foo"]) == "--output-schema=foo"
    assert find_exec_only_flag(["--model", "gpt-4"]) is None

    assert _parse_reconnect_message("Reconnecting... 2/5") == (2, 5)
    assert _parse_reconnect_message("Reconnecting... x/y") is None
    assert _parse_reconnect_message("nope") is None

    assert _short_tool_name("docs", "search") == "docs.search"
    assert _short_tool_name(None, "search") == "search"
    assert _short_tool_name(None, None) == "tool"

    summary = _summarize_tool_result({"content": ["hi"], "structured": {"ok": True}})
    assert summary == {"content_blocks": 1, "has_structured": True}
    summary = _summarize_tool_result({"content": "hello", "structured_content": None})
    assert summary == {"content_blocks": 1, "has_structured": False}
    assert _summarize_tool_result({"other": 1}) is None

    changes = [
        codex_schema.FileUpdateChange(path="a.txt", kind="update"),
        {"path": "b.txt", "kind": "delete"},
        {"path": ""},
    ]
    assert _normalize_change_list(changes) == [
        {"path": "a.txt", "kind": "update"},
        {"path": "b.txt", "kind": "delete"},
    ]
    assert _format_change_summary(changes) == "a.txt, b.txt"
    assert _format_change_summary([{"path": ""}]) == "1 files"


def test_summarize_todo_list_and_title() -> None:
    items = [
        codex_schema.TodoItem(text="first", completed=True),
        codex_schema.TodoItem(text="next", completed=False),
        {"text": "later", "completed": False},
    ]
    summary = _summarize_todo_list(items)
    assert summary.done == 1
    assert summary.total == 3
    assert summary.next_text == "next"
    assert _todo_title(summary) == "todo 1/3: next"

    done_summary = _summarize_todo_list([{"text": "done", "completed": True}])
    assert _todo_title(done_summary) == "todo 1/1: done"
    assert _todo_title(_summarize_todo_list("nope")) == "todo"


def test_select_final_answer() -> None:
    assert (
        _select_final_answer(
            [
                _AgentMessageSummary(text="working", phase="commentary"),
                _AgentMessageSummary(text="done", phase="final_answer"),
            ]
        )
        == "done"
    )

    assert (
        _select_final_answer(
            [
                _AgentMessageSummary(text="first", phase=None),
                _AgentMessageSummary(text="second", phase=None),
            ]
        )
        == "second"
    )

    assert (
        _select_final_answer([_AgentMessageSummary(text="working", phase="commentary")])
        is None
    )
    assert (
        _select_final_answer(
            [_AgentMessageSummary(text="intermediate", phase="foobar")]
        )
        is None
    )


def test_translate_codex_events_for_items() -> None:
    factory = EventFactory("codex")
    event = codex_schema.ItemStarted(
        item=codex_schema.WebSearchItem(id="w1", query="query")
    )
    out = translate_codex_event(event, title="Codex", factory=factory)
    assert len(out) == 1
    assert isinstance(out[0], ActionEvent)
    assert out[0].action.kind == "web_search"
    assert out[0].phase == "started"

    event = codex_schema.ItemCompleted(
        item=codex_schema.WebSearchItem(id="w1", query="query")
    )
    out = translate_codex_event(event, title="Codex", factory=factory)
    assert isinstance(out[0], ActionEvent)
    assert out[0].phase == "completed"
    assert out[0].ok is True

    event = codex_schema.ItemStarted(
        item=codex_schema.ReasoningItem(id="r1", text="thinking")
    )
    out = translate_codex_event(event, title="Codex", factory=factory)
    assert isinstance(out[0], ActionEvent)
    assert out[0].action.kind == "note"
    assert out[0].action.title == "thinking"

    event = codex_schema.ItemCompleted(
        item=codex_schema.AgentMessageItem(
            id="m1",
            text="working",
            phase="commentary",
        )
    )
    out = translate_codex_event(event, title="Codex", factory=factory)
    assert isinstance(out[0], ActionEvent)
    assert out[0].action.kind == "note"
    assert out[0].action.title == "working"
    assert out[0].phase == "completed"
    assert out[0].ok is True

    event = codex_schema.ItemUpdated(
        item=codex_schema.TodoListItem(
            id="t1",
            items=[
                codex_schema.TodoItem(text="todo one", completed=False),
                codex_schema.TodoItem(text="todo two", completed=True),
            ],
        )
    )
    out = translate_codex_event(event, title="Codex", factory=factory)
    assert isinstance(out[0], ActionEvent)
    assert out[0].action.detail["done"] == 1
    assert out[0].action.detail["total"] == 2
    assert "todo 1/2" in out[0].action.title

    started = codex_schema.ItemStarted(
        item=codex_schema.ErrorItem(id="e1", message="boom")
    )
    assert translate_codex_event(started, title="Codex", factory=factory) == []

    completed = codex_schema.ItemCompleted(
        item=codex_schema.ErrorItem(id="e1", message="boom")
    )
    out = translate_codex_event(completed, title="Codex", factory=factory)
    assert isinstance(out[0], ActionEvent)
    assert out[0].action.kind == "warning"
    assert out[0].ok is False


def test_translate_codex_thread_started() -> None:
    factory = EventFactory("codex")
    event = codex_schema.ThreadStarted(thread_id="sess-1")
    out = translate_codex_event(event, title="Codex", factory=factory)
    assert len(out) == 1
    assert isinstance(out[0], StartedEvent)
    assert out[0].resume.value == "sess-1"


def test_codex_runner_translate_reconnect_message() -> None:
    runner = CodexRunner(codex_cmd="codex", extra_args=[])
    state = runner.new_state("hi", None)
    event = codex_schema.StreamError(message="Reconnecting... 2/3")
    out = runner.translate(event, state=state, resume=None, found_session=None)
    assert len(out) == 1
    assert isinstance(out[0], ActionEvent)
    assert out[0].phase == "updated"
    assert out[0].action.detail["attempt"] == 2
    assert out[0].action.detail["max"] == 3


def test_codex_runner_process_and_stream_end_events() -> None:
    runner = CodexRunner(codex_cmd="codex", extra_args=[])
    state = runner.new_state("hi", None)

    out = runner.process_error_events(2, resume=None, found_session=None, state=state)
    assert len(out) == 2
    completed = out[-1]
    assert isinstance(completed, CompletedEvent)
    assert completed.ok is False

    end = runner.stream_end_events(resume=None, found_session=None, state=state)
    assert len(end) == 1
    end_event = end[0]
    assert isinstance(end_event, CompletedEvent)
    assert end_event.ok is False

    started = translate_codex_event(
        codex_schema.ThreadStarted(thread_id="sess-2"),
        title="Codex",
        factory=EventFactory("codex"),
    )[0]
    assert isinstance(started, StartedEvent)
    end = runner.stream_end_events(
        resume=None,
        found_session=started.resume,
        state=state,
    )
    end_event = end[0]
    assert isinstance(end_event, CompletedEvent)
    assert end_event.ok is True


def test_codex_build_runner_configs(tmp_path: Path) -> None:
    cfg: EngineConfig = {}
    runner = build_runner(cfg, tmp_path)
    assert isinstance(runner, CodexRunner)
    assert runner.extra_args == ["-c", "notify=[]"]

    cfg = {"extra_args": ["--foo"], "profile": "Demo"}
    runner = build_runner(cfg, tmp_path)
    assert isinstance(runner, CodexRunner)
    assert runner.extra_args[-2:] == ["--profile", "Demo"]
    assert runner.session_title == "Demo"

    with pytest.raises(ConfigError):
        build_runner({"extra_args": ["--json"]}, tmp_path)

    with pytest.raises(ConfigError):
        build_runner({"extra_args": ["--foo", 1]}, tmp_path)

    with pytest.raises(ConfigError):
        build_runner({"profile": 123}, tmp_path)
