from dataclasses import replace
from pathlib import Path

import pytest

from takopi.context import RunContext
from takopi.settings import TelegramFilesSettings
from takopi.telegram.commands import media as media_commands
from takopi.telegram.commands.file_transfer import _FilePutResult, _SavedFilePutGroup
from takopi.telegram.types import TelegramDocument, TelegramIncomingMessage
from takopi.transport_runtime import ResolvedMessage
from tests.telegram_fakes import FakeTransport, make_cfg


def _msg(
    text: str,
    *,
    message_id: int = 1,
    chat_id: int = 123,
    document: TelegramDocument | None = None,
) -> TelegramIncomingMessage:
    return TelegramIncomingMessage(
        transport="telegram",
        chat_id=chat_id,
        message_id=message_id,
        text=text,
        reply_to_message_id=None,
        reply_to_text=None,
        sender_id=1,
        document=document,
    )


@pytest.mark.anyio
async def test_media_group_empty_is_noop() -> None:
    transport = FakeTransport()
    cfg = make_cfg(transport)

    await media_commands._handle_media_group(cfg, [], topic_store=None)

    assert transport.send_calls == []


@pytest.mark.anyio
async def test_media_group_file_command_reports_usage() -> None:
    transport = FakeTransport()
    cfg = make_cfg(transport)
    msg = _msg("/file")

    await media_commands._handle_media_group(cfg, [msg], topic_store=None)

    assert transport.send_calls
    text = transport.send_calls[-1]["message"].text
    assert "usage: /file put <path>" in text
    assert "or /file get <path>" in text


@pytest.mark.anyio
async def test_media_group_file_put_delegates(monkeypatch) -> None:
    transport = FakeTransport()
    cfg = make_cfg(transport)
    msg = _msg("/file put uploads/")
    calls: dict[str, int] = {"count": 0}

    async def _fake_handle(*_args, **_kwargs) -> None:
        calls["count"] += 1

    monkeypatch.setattr(media_commands, "_handle_file_put_group", _fake_handle)

    await media_commands._handle_media_group(cfg, [msg], topic_store=None)

    assert calls["count"] == 1


@pytest.mark.anyio
async def test_media_group_auto_put_without_caption_delegates(monkeypatch) -> None:
    transport = FakeTransport()
    cfg = replace(make_cfg(transport), files=TelegramFilesSettings(enabled=True))
    msg = _msg("")
    calls: dict[str, int] = {"count": 0}

    async def _fake_handle(*_args, **_kwargs) -> None:
        calls["count"] += 1

    monkeypatch.setattr(media_commands, "_handle_file_put_group", _fake_handle)

    await media_commands._handle_media_group(cfg, [msg], topic_store=None)

    assert calls["count"] == 1


@pytest.mark.anyio
async def test_media_group_auto_put_prompt_resolve_none(monkeypatch) -> None:
    transport = FakeTransport()
    files = TelegramFilesSettings(enabled=True, auto_put=True, auto_put_mode="prompt")
    cfg = replace(make_cfg(transport), files=files)
    msg = _msg("caption")

    async def _resolve_prompt(*_args, **_kwargs):
        return None

    monkeypatch.setattr(media_commands, "_save_file_put_group", lambda *_a, **_k: None)

    await media_commands._handle_media_group(
        cfg,
        [msg],
        topic_store=None,
        resolve_prompt=_resolve_prompt,
    )

    assert transport.send_calls == []


@pytest.mark.anyio
async def test_media_group_auto_put_prompt_runs_prompt(monkeypatch) -> None:
    transport = FakeTransport()
    files = TelegramFilesSettings(enabled=True, auto_put=True, auto_put_mode="prompt")
    cfg = replace(make_cfg(transport), files=files)
    msg = _msg("caption")
    resolved = ResolvedMessage(
        prompt="do the thing",
        resume_token=None,
        engine_override=None,
        context=RunContext(project="proj"),
        context_source="directives",
    )
    saved_group = _SavedFilePutGroup(
        context=resolved.context,
        base_dir=None,
        saved=[
            _FilePutResult(
                name="a.txt",
                rel_path=Path("incoming/a.txt"),
                size=1,
                error=None,
            )
        ],
        failed=[],
    )
    prompt_calls: list[str] = []

    async def _resolve_prompt(*_args, **_kwargs):
        return resolved

    async def _save_group(*_args, **_kwargs):
        return saved_group

    async def _run_prompt(_msg, prompt: str, _resolved: ResolvedMessage) -> None:
        prompt_calls.append(prompt)

    monkeypatch.setattr(media_commands, "_save_file_put_group", _save_group)

    await media_commands._handle_media_group(
        cfg,
        [msg],
        topic_store=None,
        resolve_prompt=_resolve_prompt,
        run_prompt=_run_prompt,
    )

    assert prompt_calls
    assert "[uploaded files]" in prompt_calls[0]
    assert "incoming/a.txt" in prompt_calls[0]


@pytest.mark.anyio
async def test_media_group_auto_put_prompt_saved_failure(monkeypatch) -> None:
    transport = FakeTransport()
    files = TelegramFilesSettings(enabled=True, auto_put=True, auto_put_mode="prompt")
    cfg = replace(make_cfg(transport), files=files)
    msg = _msg("caption")
    resolved = ResolvedMessage(
        prompt="do the thing",
        resume_token=None,
        engine_override=None,
        context=RunContext(project="proj"),
        context_source="directives",
    )
    saved_group = _SavedFilePutGroup(
        context=resolved.context,
        base_dir=None,
        saved=[],
        failed=[
            _FilePutResult(
                name="a.txt",
                rel_path=None,
                size=None,
                error="boom",
            )
        ],
    )

    async def _resolve_prompt(*_args, **_kwargs):
        return resolved

    async def _save_group(*_args, **_kwargs):
        return saved_group

    monkeypatch.setattr(media_commands, "_save_file_put_group", _save_group)

    await media_commands._handle_media_group(
        cfg,
        [msg],
        topic_store=None,
        resolve_prompt=_resolve_prompt,
    )

    assert transport.send_calls
    text = transport.send_calls[-1]["message"].text
    assert "failed to upload files" in text
    assert "failed:" in text
    assert "boom" in text
