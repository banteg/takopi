from __future__ import annotations

from typing import cast

import anyio
import pytest

from takopi.runner_bridge import RunningTask, _cleanup_prompt, _wait_prompt_response
from takopi.telegram.bridge import (
    PROMPT_CALLBACK_PREFIX,
    format_prompt_text,
    handle_callback_prompt,
    prompt_markup,
)
from takopi.telegram.types import TelegramCallbackQuery
from takopi.transport import MessageRef
from tests.telegram_fakes import FakeBot, FakeTransport, make_cfg



def test_prompt_markup_default_buttons() -> None:
    mk = prompt_markup("p1", None, progress_msg_id=42)
    buttons = mk["inline_keyboard"][0]
    assert len(buttons) == 2
    assert buttons[0]["text"] == "Allow"
    assert buttons[1]["text"] == "Deny"
    assert "p1:allow" in buttons[0]["callback_data"]
    assert "p1:deny" in buttons[1]["callback_data"]


def test_prompt_markup_filters_unknown_suggestions() -> None:
    mk = prompt_markup("p2", ["allow", "magic", "deny"], progress_msg_id=10)
    buttons = mk["inline_keyboard"][0]
    labels = [b["text"] for b in buttons]
    assert labels == ["Allow", "Deny"]


def test_prompt_markup_falls_back_when_all_unknown() -> None:
    mk = prompt_markup("p3", ["foo", "bar"], progress_msg_id=10)
    buttons = mk["inline_keyboard"][0]
    labels = [b["text"] for b in buttons]
    assert labels == ["Allow", "Deny"]


def test_prompt_markup_empty_suggestions_gives_defaults() -> None:
    mk = prompt_markup("p1", [], progress_msg_id=10)
    buttons = mk["inline_keyboard"][0]
    assert len(buttons) == 2


def test_prompt_markup_callback_data_format() -> None:
    mk = prompt_markup("p1", ["allow", "deny"], progress_msg_id=999)
    buttons = mk["inline_keyboard"][0]
    assert buttons[0]["callback_data"] == f"{PROMPT_CALLBACK_PREFIX}999:p1:allow"
    assert buttons[1]["callback_data"] == f"{PROMPT_CALLBACK_PREFIX}999:p1:deny"


def test_prompt_markup_callback_data_fits_telegram_limit() -> None:
    mk = prompt_markup("p99", ["allow", "deny"], progress_msg_id=9999999999)
    for button in mk["inline_keyboard"][0]:
        assert len(button["callback_data"].encode()) <= 64



def test_format_prompt_text_bash_command() -> None:
    text = format_prompt_text("Bash", {"command": "echo hello"})
    assert "Bash" in text
    assert "echo hello" in text


def test_format_prompt_text_bash_truncates_long_command() -> None:
    long_cmd = "x" * 300
    text = format_prompt_text("Bash", {"command": long_cmd})
    assert len(text) < 300


def test_format_prompt_text_read_shows_path() -> None:
    text = format_prompt_text("Read", {"file_path": "/tmp/foo.py"})
    assert "/tmp/foo.py" in text


def test_format_prompt_text_read_falls_back_to_path_key() -> None:
    text = format_prompt_text("Read", {"path": "/tmp/bar.py"})
    assert "/tmp/bar.py" in text


def test_format_prompt_text_file_path_preferred_over_path() -> None:
    text = format_prompt_text("Edit", {"file_path": "/a.py", "path": "/b.py"})
    assert "/a.py" in text
    assert "/b.py" not in text


def test_format_prompt_text_write_no_path_keys() -> None:
    text = format_prompt_text("Write", {"content": "hello"})
    assert "Write" in text
    assert "\n" not in text  # only the header line


def test_format_prompt_text_glob_shows_pattern() -> None:
    text = format_prompt_text("Glob", {"pattern": "**/*.py"})
    assert "**/*.py" in text


def test_format_prompt_text_unknown_tool() -> None:
    text = format_prompt_text("CustomTool", {"arg": "val"})
    assert "CustomTool" in text



def _make_query(
    *,
    chat_id: int = 123,
    progress_msg_id: int = 42,
    local_id: str = "p1",
    action: str = "allow",
) -> TelegramCallbackQuery:
    data = f"{PROMPT_CALLBACK_PREFIX}{progress_msg_id}:{local_id}:{action}"
    return TelegramCallbackQuery(
        transport="telegram",
        chat_id=chat_id,
        message_id=progress_msg_id,
        callback_query_id="cbq-1",
        data=data,
        sender_id=chat_id,
    )


def _make_task_with_prompt(
    local_id: str = "p1",
) -> RunningTask:
    task = RunningTask()
    task.pending_prompts[local_id] = anyio.Event()
    task.prompt_messages[local_id] = MessageRef(channel_id=123, message_id=99)
    return task


@pytest.mark.anyio
async def test_handle_callback_prompt_allow() -> None:
    transport = FakeTransport()
    cfg = make_cfg(transport)
    task = _make_task_with_prompt()
    ref = MessageRef(channel_id=123, message_id=42)
    running_tasks = {ref: task}

    query = _make_query(action="allow")
    await handle_callback_prompt(cfg, query, running_tasks)

    assert task.pending_prompts["p1"].is_set()
    assert task.prompt_responses["p1"]["allowed"] is True
    bot = cast(FakeBot, cfg.bot)
    assert bot.callback_calls[-1]["text"] == "Allowed"


@pytest.mark.anyio
async def test_handle_callback_prompt_deny() -> None:
    transport = FakeTransport()
    cfg = make_cfg(transport)
    task = _make_task_with_prompt()
    ref = MessageRef(channel_id=123, message_id=42)
    running_tasks = {ref: task}

    query = _make_query(action="deny")
    await handle_callback_prompt(cfg, query, running_tasks)

    assert task.prompt_responses["p1"]["allowed"] is False
    bot = cast(FakeBot, cfg.bot)
    assert bot.callback_calls[-1]["text"] == "Denied"


@pytest.mark.anyio
async def test_handle_callback_prompt_unknown_action_rejected_as_invalid() -> None:
    transport = FakeTransport()
    cfg = make_cfg(transport)
    task = _make_task_with_prompt()
    ref = MessageRef(channel_id=123, message_id=42)
    running_tasks = {ref: task}

    query = _make_query(action="something_else")
    await handle_callback_prompt(cfg, query, running_tasks)

    bot = cast(FakeBot, cfg.bot)
    assert bot.callback_calls[-1]["text"] == "invalid"
    assert not task.pending_prompts["p1"].is_set()
    assert "p1" not in task.prompt_responses


@pytest.mark.anyio
async def test_handle_callback_prompt_no_task_says_session_ended() -> None:
    transport = FakeTransport()
    cfg = make_cfg(transport)

    query = _make_query()
    await handle_callback_prompt(cfg, query, {})

    bot = cast(FakeBot, cfg.bot)
    assert bot.callback_calls[-1]["text"] == "session ended"


@pytest.mark.anyio
async def test_handle_callback_prompt_expired_prompt() -> None:
    transport = FakeTransport()
    cfg = make_cfg(transport)
    task = RunningTask()  # no pending prompts
    ref = MessageRef(channel_id=123, message_id=42)
    running_tasks = {ref: task}

    query = _make_query()
    await handle_callback_prompt(cfg, query, running_tasks)

    bot = cast(FakeBot, cfg.bot)
    assert bot.callback_calls[-1]["text"] == "expired"


@pytest.mark.anyio
async def test_handle_callback_prompt_invalid_data() -> None:
    transport = FakeTransport()
    cfg = make_cfg(transport)
    query = TelegramCallbackQuery(
        transport="telegram",
        chat_id=123,
        message_id=1,
        callback_query_id="cbq-bad",
        data=f"{PROMPT_CALLBACK_PREFIX}not-enough-parts",
        sender_id=123,
    )

    await handle_callback_prompt(cfg, query, {})

    bot = cast(FakeBot, cfg.bot)
    assert bot.callback_calls[-1]["text"] == "invalid"


@pytest.mark.anyio
async def test_handle_callback_prompt_edits_prompt_message() -> None:
    transport = FakeTransport()
    cfg = make_cfg(transport)
    task = _make_task_with_prompt()
    ref = MessageRef(channel_id=123, message_id=42)
    running_tasks = {ref: task}

    query = _make_query(action="allow")
    await handle_callback_prompt(cfg, query, running_tasks)

    bot = cast(FakeBot, cfg.bot)
    assert bot.edit_calls
    edit = bot.edit_calls[-1]
    assert edit["message_id"] == 99
    assert "Allowed" in edit["text"]
    assert edit["reply_markup"] is not None  # CLEAR_MARKUP


@pytest.mark.anyio
async def test_handle_callback_prompt_deny_edits_with_denied_icon() -> None:
    transport = FakeTransport()
    cfg = make_cfg(transport)
    task = _make_task_with_prompt()
    ref = MessageRef(channel_id=123, message_id=42)
    running_tasks = {ref: task}

    query = _make_query(action="deny")
    await handle_callback_prompt(cfg, query, running_tasks)

    bot = cast(FakeBot, cfg.bot)
    assert bot.edit_calls
    edit = bot.edit_calls[-1]
    assert "Denied" in edit["text"]
    assert "\u2713" not in edit["text"]  # no checkmark on deny



@pytest.mark.anyio
async def test_wait_prompt_response_allow() -> None:
    task = RunningTask()
    task.pending_prompts["p1"] = anyio.Event()
    written: list[dict[str, object]] = []

    async def fake_respond(data: dict[str, object]) -> None:
        written.append(data)

    task.prompt_responders["p1"] = fake_respond

    # Simulate user response in a separate task
    async with anyio.create_task_group() as tg:

        async def respond_soon() -> None:
            await anyio.sleep(0.01)
            task.prompt_responses["p1"] = {"allowed": True}
            task.pending_prompts["p1"].set()

        tg.start_soon(respond_soon)
        await _wait_prompt_response(task, "p1", timeout_s=5.0)

    assert len(written) == 1
    assert written[0] == {"allowed": True}
    # cleanup happened
    assert "p1" not in task.pending_prompts


@pytest.mark.anyio
async def test_wait_prompt_response_deny() -> None:
    task = RunningTask()
    task.pending_prompts["p1"] = anyio.Event()
    written: list[dict[str, object]] = []

    async def fake_respond(data: dict[str, object]) -> None:
        written.append(data)

    task.prompt_responders["p1"] = fake_respond

    async with anyio.create_task_group() as tg:

        async def respond_soon() -> None:
            await anyio.sleep(0.01)
            task.prompt_responses["p1"] = {"allowed": False}
            task.pending_prompts["p1"].set()

        tg.start_soon(respond_soon)
        await _wait_prompt_response(task, "p1", timeout_s=5.0)

    assert len(written) == 1
    assert written[0] == {"error": "denied by user"}


@pytest.mark.anyio
async def test_wait_prompt_response_timeout() -> None:
    task = RunningTask()
    task.pending_prompts["p1"] = anyio.Event()
    written: list[dict[str, object]] = []

    async def fake_respond(data: dict[str, object]) -> None:
        written.append(data)

    task.prompt_responders["p1"] = fake_respond

    await _wait_prompt_response(task, "p1", timeout_s=0.01)

    assert len(written) == 1
    assert written[0] == {"error": "timed out"}
    # cleanup happened
    assert "p1" not in task.pending_prompts


@pytest.mark.anyio
async def test_wait_prompt_response_timeout_edits_message() -> None:
    transport = FakeTransport()
    task = RunningTask()
    task.pending_prompts["p1"] = anyio.Event()
    prompt_ref = MessageRef(channel_id=123, message_id=99)
    task.prompt_messages["p1"] = prompt_ref
    written: list[dict[str, object]] = []

    async def fake_respond(data: dict[str, object]) -> None:
        written.append(data)

    task.prompt_responders["p1"] = fake_respond

    await _wait_prompt_response(task, "p1", timeout_s=0.01, transport=transport)

    assert len(written) == 1
    assert written[0] == {"error": "timed out"}
    # prompt message was edited to clear buttons
    assert len(transport.edit_calls) == 1
    edit = transport.edit_calls[0]
    assert edit["ref"] == prompt_ref
    assert edit["message"].text == "timed out"
    assert edit["message"].extra["reply_markup"] == {"inline_keyboard": []}
    # cleanup happened
    assert "p1" not in task.pending_prompts
    assert "p1" not in task.prompt_messages


@pytest.mark.anyio
async def test_wait_prompt_response_cleans_up_on_completion() -> None:
    task = RunningTask()
    task.pending_prompts["p1"] = anyio.Event()
    task.prompt_responders["p1"] = lambda _: None  # type: ignore[return-value, arg-type]

    task.prompt_responses["p1"] = {"allowed": True}
    task.pending_prompts["p1"].set()

    await _wait_prompt_response(task, "p1", timeout_s=5.0)

    assert "p1" not in task.pending_prompts
    assert "p1" not in task.prompt_responses
    assert "p1" not in task.prompt_responders



def test_cleanup_prompt_removes_all_state() -> None:
    task = RunningTask()
    task.pending_prompts["p1"] = anyio.Event()
    task.prompt_responses["p1"] = {"allowed": True}
    task.prompt_messages["p1"] = MessageRef(channel_id=1, message_id=1)

    _cleanup_prompt(task, "p1")

    assert "p1" not in task.pending_prompts
    assert "p1" not in task.prompt_responses
    assert "p1" not in task.prompt_messages


def test_cleanup_prompt_ignores_missing_keys() -> None:
    task = RunningTask()
    _cleanup_prompt(task, "p99")  # should not raise
