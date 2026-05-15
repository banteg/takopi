from __future__ import annotations

from pathlib import Path

import anyio
import pytest

from takopi.backends import EngineConfig
from takopi.config import ConfigError
from takopi.events import EventFactory
from takopi.model import ActionEvent, CompletedEvent, StartedEvent
from takopi.runners.codex import (
    _AgentMessageSummary,
    _AppServerClient,
    _AppServerRunState,
    AppServerCodexRunner,
    CodexRunner,
    _format_change_summary,
    _normalize_change_list,
    _parse_reconnect_message,
    _select_final_answer,
    _short_tool_name,
    _summarize_todo_list,
    _summarize_tool_result,
    _todo_title,
    _translate_app_item_event,
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


def test_translate_app_server_context_compaction_item() -> None:
    state = _AppServerRunState(factory=EventFactory("codex"))
    item = {"id": "cc1", "type": "contextCompaction"}

    out = _translate_app_item_event("item/started", item, state=state)
    assert len(out) == 1
    assert isinstance(out[0], ActionEvent)
    assert out[0].phase == "started"
    assert out[0].action.kind == "note"
    assert out[0].action.title == "compacting context"

    out = _translate_app_item_event("item/completed", item, state=state)
    assert len(out) == 1
    assert isinstance(out[0], ActionEvent)
    assert out[0].phase == "completed"
    assert out[0].ok is True
    assert out[0].action.title == "compacting context"


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


@pytest.mark.anyio
async def test_app_server_client_fails_waiters_on_clean_eof(tmp_path: Path) -> None:
    codex_path = tmp_path / "codex"
    codex_path.write_text(
        "#!/usr/bin/env python3\nimport sys\n\nfor _line in sys.stdin:\n    break\n",
        encoding="utf-8",
    )
    codex_path.chmod(0o755)
    client = _AppServerClient(codex_cmd=str(codex_path), extra_args=[])

    with anyio.fail_after(2), pytest.raises(RuntimeError, match="closed stdout"):
        await client.start()


def test_app_server_client_handles_server_requests() -> None:
    client = _AppServerClient(codex_cmd="codex", extra_args=[])

    assert client._handle_server_request(
        {"method": "item/commandExecution/requestApproval"}
    ) == {"decision": "accept"}
    assert client._handle_server_request(
        {"method": "item/fileChange/requestApproval"}
    ) == {"decision": "accept"}
    assert client._handle_server_request(
        {
            "method": "item/permissions/requestApproval",
            "params": {"permissions": {"sandbox": "workspace-write"}},
        }
    ) == {"scope": "turn", "permissions": {"sandbox": "workspace-write"}}
    assert client._handle_server_request(
        {"method": "mcpServer/elicitation/request"}
    ) == {"action": "decline", "content": None}
    assert client._handle_server_request({"method": "other"}) == {}


@pytest.mark.anyio
async def test_app_server_runner_raises_on_turn_stream_eof(
    tmp_path: Path,
) -> None:
    codex_path = tmp_path / "codex"
    codex_path.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        "import sys\n"
        "\n"
        "def send(payload):\n"
        "    print(json.dumps(payload), flush=True)\n"
        "\n"
        "for line in sys.stdin:\n"
        "    msg = json.loads(line)\n"
        "    method = msg.get('method')\n"
        "    req_id = msg.get('id')\n"
        "    if method == 'initialize':\n"
        "        send({'id': req_id, 'result': {'serverInfo': {'name': 'fake'}}})\n"
        "    elif method == 'initialized':\n"
        "        pass\n"
        "    elif method == 'thread/start':\n"
        "        send({'id': req_id, 'result': {'thread': {'id': 'thread-1'}}})\n"
        "    elif method == 'turn/start':\n"
        "        turn = {'id': 'turn-1', 'status': 'running', 'items': []}\n"
        "        send({'id': req_id, 'result': {'turn': turn}})\n"
        "        send({'method': 'turn/started', 'params': {'threadId': 'thread-1', 'turn': turn}})\n"
        "        break\n",
        encoding="utf-8",
    )
    codex_path.chmod(0o755)
    runner = AppServerCodexRunner(codex_cmd=str(codex_path), extra_args=[])

    stream = runner.run("hello", None)
    with anyio.fail_after(2):
        started = await anext(stream)
        with pytest.raises(RuntimeError, match="closed before turn completed"):
            async for _event in stream:
                pass

    assert isinstance(started, StartedEvent)
    assert started.resume.value == "thread-1"


@pytest.mark.anyio
async def test_app_server_codex_runner_translates_turn_notifications(
    tmp_path: Path,
) -> None:
    codex_path = tmp_path / "codex"
    codex_path.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        "import sys\n"
        "\n"
        "def send(payload):\n"
        "    print(json.dumps(payload), flush=True)\n"
        "\n"
        "for line in sys.stdin:\n"
        "    msg = json.loads(line)\n"
        "    method = msg.get('method')\n"
        "    req_id = msg.get('id')\n"
        "    if method == 'initialize':\n"
        "        assert msg['params']['clientInfo']['version']\n"
        "        send({'id': req_id, 'result': {'serverInfo': {'name': 'fake'}}})\n"
        "    elif method == 'initialized':\n"
        "        pass\n"
        "    elif method == 'thread/start':\n"
        "        send({'id': req_id, 'result': {'thread': {'id': 'thread-1'}}})\n"
        "    elif method == 'turn/start':\n"
        "        turn = {'id': 'turn-1', 'status': 'running', 'items': []}\n"
        "        send({'id': req_id, 'result': {'turn': turn}})\n"
        "        send({'method': 'turn/started', 'params': {'threadId': 'thread-1', 'turn': turn}})\n"
        "        send({'method': 'item/started', 'params': {'threadId': 'thread-1', 'turnId': 'turn-1', 'item': {'id': 'r1', 'type': 'reasoning', 'summary': []}}})\n"
        "        send({'method': 'item/completed', 'params': {'threadId': 'thread-1', 'turnId': 'turn-1', 'item': {'id': 'r1', 'type': 'reasoning', 'summary': []}}})\n"
        "        send({'method': 'item/started', 'params': {'threadId': 'thread-1', 'turnId': 'turn-1', 'item': {'id': 'c1', 'type': 'agentMessage', 'phase': 'commentary', 'text': ''}}})\n"
        "        send({'method': 'item/agentMessage/delta', 'params': {'threadId': 'thread-1', 'turnId': 'turn-1', 'itemId': 'c1', 'delta': 'work'}})\n"
        "        send({'method': 'item/agentMessage/delta', 'params': {'threadId': 'thread-1', 'turnId': 'turn-1', 'itemId': 'c1', 'delta': 'ing'}})\n"
        "        send({'method': 'item/completed', 'params': {'threadId': 'thread-1', 'turnId': 'turn-1', 'item': {'id': 'c1', 'type': 'agentMessage', 'phase': 'commentary', 'text': 'working'}}})\n"
        "        send({'method': 'item/completed', 'params': {'threadId': 'thread-1', 'turnId': 'turn-1', 'item': {'id': 'a1', 'type': 'agentMessage', 'phase': 'final_answer', 'text': 'done'}}})\n"
        "        send({'method': 'turn/completed', 'params': {'threadId': 'thread-1', 'turn': {'id': 'turn-1', 'status': 'completed', 'items': []}}})\n"
        "        break\n",
        encoding="utf-8",
    )
    codex_path.chmod(0o755)
    runner = AppServerCodexRunner(codex_cmd=str(codex_path), extra_args=[])

    with anyio.fail_after(2):
        events = [event async for event in runner.run("hello", None)]

    assert isinstance(events[0], StartedEvent)
    assert events[0].resume.value == "thread-1"
    assert events[0].meta is not None
    assert events[0].meta["turn_id"] == "turn-1"
    event_summary = [
        (
            event.type,
            getattr(event, "phase", None),
            getattr(getattr(event, "action", None), "kind", None),
            getattr(getattr(event, "action", None), "title", None),
        )
        for event in events
    ]
    assert any(
        isinstance(event, ActionEvent)
        and event.phase == "started"
        and event.action.kind == "note"
        and event.action.title == "work"
        and event.action.detail == {"phase": "commentary"}
        for event in events
    ), event_summary
    assert any(
        isinstance(event, ActionEvent)
        and event.phase == "updated"
        and event.action.kind == "note"
        and event.action.title == "working"
        and event.action.detail == {"phase": "commentary"}
        for event in events
    ), event_summary
    assert not any(
        isinstance(event, ActionEvent) and event.action.title == "commentary"
        for event in events
    ), event_summary
    assert not any(
        isinstance(event, ActionEvent) and event.action.title == "reasoning"
        for event in events
    ), event_summary
    completed = events[-1]
    assert isinstance(completed, CompletedEvent)
    assert completed.ok is True
    assert completed.answer == "done"


def test_codex_build_runner_configs(tmp_path: Path) -> None:
    cfg: EngineConfig = {}
    runner = build_runner(cfg, tmp_path)
    assert isinstance(runner, AppServerCodexRunner)
    assert runner.extra_args == ["-c", "notify=[]"]

    cfg = {"extra_args": ["--foo"], "profile": "Demo"}
    runner = build_runner(cfg, tmp_path)
    assert isinstance(runner, AppServerCodexRunner)
    assert runner.extra_args[-2:] == ["--profile", "Demo"]
    assert runner.session_title == "Demo"

    runner = build_runner({"mode": "exec"}, tmp_path)
    assert isinstance(runner, CodexRunner)

    with pytest.raises(ConfigError):
        build_runner({"mode": "unknown"}, tmp_path)

    with pytest.raises(ConfigError):
        build_runner({"extra_args": ["--json"]}, tmp_path)

    with pytest.raises(ConfigError):
        build_runner({"extra_args": ["--foo", 1]}, tmp_path)

    with pytest.raises(ConfigError):
        build_runner({"profile": 123}, tmp_path)
