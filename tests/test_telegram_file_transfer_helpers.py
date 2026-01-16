from dataclasses import replace
from pathlib import Path

import pytest

from takopi.config import ProjectConfig, ProjectsConfig
from takopi.context import RunContext
from takopi.router import AutoRouter, RunnerEntry
from takopi.runners.mock import Return, ScriptRunner
from takopi.telegram.api_models import ChatMember, File
from takopi.settings import TelegramFilesSettings
from takopi.telegram.commands import file_transfer as transfer
from takopi.telegram.types import TelegramDocument, TelegramIncomingMessage
from takopi.transport_runtime import ResolvedMessage, TransportRuntime
from tests.telegram_fakes import DEFAULT_ENGINE_ID, FakeBot, FakeTransport, make_cfg


class _FileBot(FakeBot):
    def __init__(self, *, file_info: File | None, payload: bytes | None) -> None:
        super().__init__()
        self._file_info = file_info
        self._payload = payload

    async def get_file(self, file_id: str) -> File | None:
        _ = file_id
        return self._file_info

    async def download_file(self, file_path: str) -> bytes | None:
        _ = file_path
        return self._payload


def _document(
    *,
    file_id: str = "file",
    file_name: str | None = "upload.bin",
    file_size: int | None = 1,
) -> TelegramDocument:
    return TelegramDocument(
        file_id=file_id,
        file_name=file_name,
        mime_type="application/octet-stream",
        file_size=file_size,
        raw={},
    )


def _msg(
    text: str,
    *,
    message_id: int = 1,
    chat_id: int = 123,
    sender_id: int | None = 1,
    chat_type: str | None = None,
    document: TelegramDocument | None = None,
) -> TelegramIncomingMessage:
    return TelegramIncomingMessage(
        transport="telegram",
        chat_id=chat_id,
        message_id=message_id,
        text=text,
        reply_to_message_id=None,
        reply_to_text=None,
        sender_id=sender_id,
        chat_type=chat_type,
        document=document,
    )


def _runtime(tmp_path: Path) -> TransportRuntime:
    runner = ScriptRunner([Return(answer="ok")], engine=DEFAULT_ENGINE_ID)
    router = AutoRouter(
        entries=[RunnerEntry(engine=runner.engine, runner=runner)],
        default_engine=runner.engine,
    )
    projects = ProjectsConfig(
        projects={
            "proj": ProjectConfig(
                alias="proj",
                path=tmp_path,
                worktrees_dir=Path(".worktrees"),
            )
        },
        default_project="proj",
    )
    return TransportRuntime(router=router, projects=projects)


def _resolved() -> ResolvedMessage:
    return ResolvedMessage(
        prompt="",
        resume_token=None,
        engine_override=None,
        context=None,
        context_source="none",
    )


def _plan(tmp_path: Path, *, path_value: str | None) -> transfer._FilePutPlan:
    return transfer._FilePutPlan(
        resolved=_resolved(),
        run_root=tmp_path,
        path_value=path_value,
        force=False,
    )


@pytest.mark.anyio
async def test_save_document_payload_rejects_large_file(tmp_path: Path) -> None:
    transport = FakeTransport()
    cfg = replace(
        make_cfg(transport),
        bot=_FileBot(file_info=None, payload=None),
    )
    document = _document(file_size=TelegramFilesSettings.max_upload_bytes + 1)

    result = await transfer._save_document_payload(
        cfg,
        document=document,
        run_root=tmp_path,
        rel_path=None,
        base_dir=None,
        force=False,
    )

    assert result.error == "file is too large to upload."


@pytest.mark.anyio
async def test_save_document_payload_denied_path(tmp_path: Path) -> None:
    transport = FakeTransport()
    cfg = replace(
        make_cfg(transport),
        bot=_FileBot(file_info=File(file_path="files/x.bin"), payload=None),
    )
    document = _document(file_name="x.bin")

    result = await transfer._save_document_payload(
        cfg,
        document=document,
        run_root=tmp_path,
        rel_path=None,
        base_dir=Path(".git"),
        force=False,
    )

    assert result.error == "path denied by rule: .git/**"


@pytest.mark.anyio
async def test_save_document_payload_existing_file(tmp_path: Path) -> None:
    transport = FakeTransport()
    cfg = replace(
        make_cfg(transport),
        bot=_FileBot(file_info=File(file_path="files/report.txt"), payload=None),
    )
    document = _document(file_name="report.txt")
    target = tmp_path / cfg.files.uploads_dir / "report.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("existing", encoding="utf-8")

    result = await transfer._save_document_payload(
        cfg,
        document=document,
        run_root=tmp_path,
        rel_path=None,
        base_dir=None,
        force=False,
    )

    assert result.error == "file already exists; use --force to overwrite."


@pytest.mark.anyio
async def test_save_document_payload_success(tmp_path: Path) -> None:
    transport = FakeTransport()
    payload = b"hello"
    cfg = replace(
        make_cfg(transport),
        bot=_FileBot(file_info=File(file_path="files/report.txt"), payload=payload),
    )
    document = _document(file_name="report.txt")

    result = await transfer._save_document_payload(
        cfg,
        document=document,
        run_root=tmp_path,
        rel_path=None,
        base_dir=None,
        force=False,
    )

    assert result.error is None
    assert result.rel_path is not None
    assert (tmp_path / result.rel_path).read_bytes() == payload


@pytest.mark.anyio
async def test_save_document_payload_missing_metadata(tmp_path: Path) -> None:
    transport = FakeTransport()
    cfg = replace(
        make_cfg(transport),
        bot=_FileBot(file_info=None, payload=None),
    )
    document = _document()

    result = await transfer._save_document_payload(
        cfg,
        document=document,
        run_root=tmp_path,
        rel_path=None,
        base_dir=None,
        force=False,
    )

    assert result.error == "failed to fetch file metadata."


@pytest.mark.anyio
async def test_save_document_payload_download_failed(tmp_path: Path) -> None:
    transport = FakeTransport()
    cfg = replace(
        make_cfg(transport),
        bot=_FileBot(file_info=File(file_path="files/report.txt"), payload=None),
    )
    document = _document(file_name="report.txt")

    result = await transfer._save_document_payload(
        cfg,
        document=document,
        run_root=tmp_path,
        rel_path=None,
        base_dir=None,
        force=False,
    )

    assert result.error == "failed to download file."


@pytest.mark.anyio
async def test_save_document_payload_target_is_dir(tmp_path: Path) -> None:
    transport = FakeTransport()
    cfg = replace(
        make_cfg(transport),
        bot=_FileBot(file_info=File(file_path="files/report.txt"), payload=b"payload"),
    )
    document = _document(file_name="report.txt")
    target = tmp_path / "uploads"
    target.mkdir()

    result = await transfer._save_document_payload(
        cfg,
        document=document,
        run_root=tmp_path,
        rel_path=Path("uploads"),
        base_dir=None,
        force=False,
    )

    assert result.error == "upload target is a directory."


def test_resolve_file_put_paths_invalid_dir(tmp_path: Path) -> None:
    transport = FakeTransport()
    cfg = make_cfg(transport)
    plan = _plan(tmp_path, path_value="../escape/")

    base_dir, rel_path, error = transfer.resolve_file_put_paths(
        plan,
        cfg=cfg,
        require_dir=True,
    )

    assert base_dir is None
    assert rel_path is None
    assert error == "invalid upload path."


def test_resolve_file_put_paths_denied_rule(tmp_path: Path) -> None:
    transport = FakeTransport()
    cfg = make_cfg(transport)
    plan = _plan(tmp_path, path_value=".env/")

    base_dir, rel_path, error = transfer.resolve_file_put_paths(
        plan,
        cfg=cfg,
        require_dir=True,
    )

    assert base_dir is None
    assert rel_path is None
    assert error == "path denied by rule: .env"


def test_resolve_file_put_paths_target_is_file(tmp_path: Path) -> None:
    transport = FakeTransport()
    cfg = make_cfg(transport)
    target = tmp_path / "uploads"
    target.write_text("data", encoding="utf-8")
    plan = _plan(tmp_path, path_value="uploads/")

    base_dir, rel_path, error = transfer.resolve_file_put_paths(
        plan,
        cfg=cfg,
        require_dir=True,
    )

    assert base_dir is None
    assert rel_path is None
    assert error == "upload path is a file."


def test_resolve_file_put_paths_invalid_rel_path(tmp_path: Path) -> None:
    transport = FakeTransport()
    cfg = make_cfg(transport)
    plan = _plan(tmp_path, path_value="~/secret.txt")

    base_dir, rel_path, error = transfer.resolve_file_put_paths(
        plan,
        cfg=cfg,
        require_dir=False,
    )

    assert base_dir is None
    assert rel_path is None
    assert error == "invalid upload path."


@pytest.mark.anyio
async def test_check_file_permissions_requires_sender(tmp_path: Path) -> None:
    transport = FakeTransport()
    cfg = make_cfg(transport)
    msg = _msg("/file put", sender_id=None)

    allowed = await transfer._check_file_permissions(cfg, msg)

    assert allowed is False
    assert transport.send_calls
    assert "cannot verify sender" in transport.send_calls[-1]["message"].text


@pytest.mark.anyio
async def test_check_file_permissions_denies_unlisted_user(tmp_path: Path) -> None:
    transport = FakeTransport()
    files = TelegramFilesSettings(allowed_user_ids=[42])
    cfg = replace(make_cfg(transport), files=files)
    msg = _msg("/file put", sender_id=1)

    allowed = await transfer._check_file_permissions(cfg, msg)

    assert allowed is False
    assert transport.send_calls
    assert "file transfer is not allowed" in transport.send_calls[-1]["message"].text


@pytest.mark.anyio
async def test_check_file_permissions_denies_non_admin(tmp_path: Path) -> None:
    class _MemberBot(FakeBot):
        async def get_chat_member(self, chat_id: int, user_id: int):
            _ = chat_id
            _ = user_id
            return ChatMember(status="member")

    transport = FakeTransport()
    cfg = replace(make_cfg(transport), bot=_MemberBot())
    msg = _msg("/file put", chat_id=-123, chat_type="group")

    allowed = await transfer._check_file_permissions(cfg, msg)

    assert allowed is False
    assert transport.send_calls
    assert (
        "file transfer is restricted to group admins"
        in transport.send_calls[-1]["message"].text
    )


@pytest.mark.anyio
async def test_prepare_file_put_plan_denied_user(tmp_path: Path) -> None:
    transport = FakeTransport()
    files = TelegramFilesSettings(allowed_user_ids=[42])
    cfg = replace(make_cfg(transport), files=files, runtime=_runtime(tmp_path))
    msg = _msg("/file put", sender_id=1)

    plan = await transfer._prepare_file_put_plan(
        cfg,
        msg,
        "note.txt",
        ambient_context=None,
        topic_store=None,
    )

    assert plan is None
    assert transport.send_calls
    assert "file transfer is not allowed" in transport.send_calls[-1]["message"].text


@pytest.mark.anyio
async def test_prepare_file_put_plan_directive_error(tmp_path: Path) -> None:
    transport = FakeTransport()
    cfg = replace(make_cfg(transport), runtime=_runtime(tmp_path))
    msg = _msg("/file put")

    plan = await transfer._prepare_file_put_plan(
        cfg,
        msg,
        "/proj /proj note.txt",
        ambient_context=None,
        topic_store=None,
    )

    assert plan is None
    assert transport.send_calls
    assert "multiple project directives" in transport.send_calls[-1]["message"].text


@pytest.mark.anyio
async def test_prepare_file_put_plan_requires_context(tmp_path: Path) -> None:
    transport = FakeTransport()
    cfg = make_cfg(transport)
    msg = _msg("/file put")

    plan = await transfer._prepare_file_put_plan(
        cfg,
        msg,
        "note.txt",
        ambient_context=None,
        topic_store=None,
    )

    assert plan is None
    assert transport.send_calls
    assert (
        "no project context available for file upload"
        in transport.send_calls[-1]["message"].text
    )


@pytest.mark.anyio
async def test_prepare_file_put_plan_rejects_unknown_flag(tmp_path: Path) -> None:
    transport = FakeTransport()
    cfg = replace(make_cfg(transport), runtime=_runtime(tmp_path))
    msg = _msg("/file put")

    plan = await transfer._prepare_file_put_plan(
        cfg,
        msg,
        "--bogus note.txt",
        ambient_context=None,
        topic_store=None,
    )

    assert plan is None
    assert transport.send_calls
    assert "unknown flag" in transport.send_calls[-1]["message"].text


@pytest.mark.anyio
async def test_save_file_put_group_requires_documents(tmp_path: Path) -> None:
    transport = FakeTransport()
    cfg = replace(make_cfg(transport), runtime=_runtime(tmp_path))
    msg = _msg("/file put")

    result = await transfer._save_file_put_group(
        cfg,
        msg,
        "",
        [],
        ambient_context=None,
        topic_store=None,
    )

    assert result is None
    assert transport.send_calls
    assert "usage: /file put <path>" in transport.send_calls[-1]["message"].text


@pytest.mark.anyio
async def test_save_file_put_group_saves_documents(tmp_path: Path) -> None:
    transport = FakeTransport()
    cfg = replace(
        make_cfg(transport),
        runtime=_runtime(tmp_path),
        bot=_FileBot(file_info=File(file_path="files/doc.bin"), payload=b"payload"),
    )
    msg = _msg(
        "/file put uploads/",
        document=_document(file_id="a", file_name="a.txt"),
    )
    extra = _msg(
        "/file put uploads/",
        message_id=2,
        document=_document(file_id="b", file_name="b.txt"),
    )

    result = await transfer._save_file_put_group(
        cfg,
        msg,
        "uploads/",
        [msg, extra],
        ambient_context=None,
        topic_store=None,
    )

    assert result is not None
    assert result.base_dir == Path("uploads")
    assert [item.name for item in result.saved] == ["a.txt", "b.txt"]
    assert result.failed == []
    assert (tmp_path / "uploads" / "a.txt").read_bytes() == b"payload"
    assert (tmp_path / "uploads" / "b.txt").read_bytes() == b"payload"


@pytest.mark.anyio
async def test_handle_file_put_saves_and_replies(tmp_path: Path) -> None:
    transport = FakeTransport()
    cfg = replace(
        make_cfg(transport),
        runtime=_runtime(tmp_path),
        bot=_FileBot(file_info=File(file_path="files/note.txt"), payload=b"hello"),
    )
    msg = _msg("/file put note.txt", document=_document(file_name="note.txt"))

    await transfer._handle_file_put(
        cfg,
        msg,
        "note.txt",
        ambient_context=None,
        topic_store=None,
    )

    assert (tmp_path / "note.txt").read_bytes() == b"hello"
    assert transport.send_calls
    assert "saved note.txt" in transport.send_calls[-1]["message"].text


@pytest.mark.anyio
async def test_handle_file_put_default_delegates(monkeypatch) -> None:
    transport = FakeTransport()
    cfg = make_cfg(transport)
    msg = _msg("/file put")
    called: dict[str, int] = {"count": 0}

    async def _fake_handle(*_args, **_kwargs) -> None:
        called["count"] += 1

    monkeypatch.setattr(transfer, "_handle_file_put", _fake_handle)

    await transfer._handle_file_put_default(
        cfg,
        msg,
        ambient_context=None,
        topic_store=None,
    )

    assert called["count"] == 1


@pytest.mark.anyio
async def test_handle_file_command_routes(monkeypatch) -> None:
    transport = FakeTransport()
    cfg = make_cfg(transport)
    msg = _msg("/file")
    calls: dict[str, int] = {"put": 0, "get": 0}

    async def _fake_put(*_args, **_kwargs) -> None:
        calls["put"] += 1

    async def _fake_get(*_args, **_kwargs) -> None:
        calls["get"] += 1

    monkeypatch.setattr(transfer, "_handle_file_put", _fake_put)
    monkeypatch.setattr(transfer, "_handle_file_get", _fake_get)

    await transfer._handle_file_command(
        cfg,
        msg,
        "put uploads/",
        ambient_context=None,
        topic_store=None,
    )
    await transfer._handle_file_command(
        cfg,
        msg,
        "get downloads/report.txt",
        ambient_context=None,
        topic_store=None,
    )

    assert calls["put"] == 1
    assert calls["get"] == 1


@pytest.mark.anyio
async def test_handle_file_command_invalid_usage() -> None:
    transport = FakeTransport()
    cfg = make_cfg(transport)
    msg = _msg("/file")

    await transfer._handle_file_command(
        cfg,
        msg,
        "unknown arg",
        ambient_context=None,
        topic_store=None,
    )

    assert transport.send_calls
    assert "usage: /file put <path>" in transport.send_calls[-1]["message"].text


@pytest.mark.anyio
async def test_handle_file_put_group_formats_failures(
    tmp_path: Path, monkeypatch
) -> None:
    transport = FakeTransport()
    cfg = replace(make_cfg(transport), runtime=_runtime(tmp_path))
    msg = _msg("/file put uploads/")
    saved_group = transfer._SavedFilePutGroup(
        context=RunContext(project="proj", branch=None),
        base_dir=Path("uploads"),
        saved=[
            transfer._FilePutResult(
                name="a.txt",
                rel_path=Path("uploads/a.txt"),
                size=1,
                error=None,
            )
        ],
        failed=[
            transfer._FilePutResult(
                name="b.txt",
                rel_path=None,
                size=None,
                error="boom",
            )
        ],
    )

    async def _fake_save(*_args, **_kwargs):
        return saved_group

    monkeypatch.setattr(transfer, "_save_file_put_group", _fake_save)

    await transfer._handle_file_put_group(
        cfg,
        msg,
        "uploads/",
        [msg],
        ambient_context=None,
        topic_store=None,
    )

    assert transport.send_calls
    text = transport.send_calls[-1]["message"].text
    assert "saved a.txt to uploads/" in text
    assert "failed:" in text


@pytest.mark.anyio
async def test_handle_file_get_requires_path(tmp_path: Path) -> None:
    transport = FakeTransport()
    cfg = replace(make_cfg(transport), runtime=_runtime(tmp_path))
    msg = _msg("/file get")

    await transfer._handle_file_get(
        cfg,
        msg,
        "",
        ambient_context=None,
        topic_store=None,
    )

    assert transport.send_calls
    assert "usage: /file get <path>" in transport.send_calls[-1]["message"].text


@pytest.mark.anyio
async def test_handle_file_get_invalid_path(tmp_path: Path) -> None:
    transport = FakeTransport()
    cfg = replace(make_cfg(transport), runtime=_runtime(tmp_path))
    msg = _msg("/file get")

    await transfer._handle_file_get(
        cfg,
        msg,
        "../secret.txt",
        ambient_context=None,
        topic_store=None,
    )

    assert transport.send_calls
    assert "invalid download path" in transport.send_calls[-1]["message"].text


@pytest.mark.anyio
async def test_handle_file_get_missing_file(tmp_path: Path) -> None:
    transport = FakeTransport()
    cfg = replace(make_cfg(transport), runtime=_runtime(tmp_path))
    msg = _msg("/file get")

    await transfer._handle_file_get(
        cfg,
        msg,
        "missing.txt",
        ambient_context=None,
        topic_store=None,
    )

    assert transport.send_calls
    assert "file does not exist" in transport.send_calls[-1]["message"].text


@pytest.mark.anyio
async def test_handle_file_get_sends_file(tmp_path: Path) -> None:
    transport = FakeTransport()
    bot = FakeBot()
    cfg = replace(make_cfg(transport), runtime=_runtime(tmp_path), bot=bot)
    target = tmp_path / "notes.txt"
    target.write_bytes(b"hello")
    msg = _msg("/file get")

    await transfer._handle_file_get(
        cfg,
        msg,
        "notes.txt",
        ambient_context=None,
        topic_store=None,
    )

    assert bot.document_calls
    call = bot.document_calls[-1]
    assert call["filename"] == "notes.txt"
    assert call["content"] == b"hello"


@pytest.mark.anyio
async def test_handle_file_get_sends_directory_zip(tmp_path: Path) -> None:
    transport = FakeTransport()
    bot = FakeBot()
    cfg = replace(make_cfg(transport), runtime=_runtime(tmp_path), bot=bot)
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "file.txt").write_text("data", encoding="utf-8")
    msg = _msg("/file get")

    await transfer._handle_file_get(
        cfg,
        msg,
        "bundle",
        ambient_context=None,
        topic_store=None,
    )

    assert bot.document_calls
    call = bot.document_calls[-1]
    assert call["filename"] == "bundle.zip"
    assert call["content"][:2] == b"PK"


@pytest.mark.anyio
async def test_save_document_payload_rejects_large_payload(
    tmp_path: Path, monkeypatch
) -> None:
    transport = FakeTransport()
    cfg = replace(
        make_cfg(transport),
        bot=_FileBot(file_info=File(file_path="files/report.txt"), payload=b"xx"),
    )
    document = _document(file_name="report.txt", file_size=None)
    monkeypatch.setattr(TelegramFilesSettings, "max_upload_bytes", 1)

    result = await transfer._save_document_payload(
        cfg,
        document=document,
        run_root=tmp_path,
        rel_path=None,
        base_dir=None,
        force=False,
    )

    assert result.error == "file is too large to upload."


@pytest.mark.anyio
async def test_save_document_payload_write_error(tmp_path: Path, monkeypatch) -> None:
    transport = FakeTransport()
    cfg = replace(
        make_cfg(transport),
        bot=_FileBot(file_info=File(file_path="files/report.txt"), payload=b"data"),
    )
    document = _document(file_name="report.txt")

    def _raise(*_args, **_kwargs):
        raise OSError("boom")

    monkeypatch.setattr(transfer, "write_bytes_atomic", _raise)

    result = await transfer._save_document_payload(
        cfg,
        document=document,
        run_root=tmp_path,
        rel_path=None,
        base_dir=None,
        force=False,
    )

    assert result.error == "failed to write file: boom"


@pytest.mark.anyio
async def test_check_file_permissions_missing_member(tmp_path: Path) -> None:
    class _NoMemberBot(FakeBot):
        async def get_chat_member(self, chat_id: int, user_id: int):
            _ = chat_id
            _ = user_id
            return None

    transport = FakeTransport()
    cfg = replace(make_cfg(transport), bot=_NoMemberBot())
    msg = _msg("/file put", chat_id=-123, chat_type="group")

    allowed = await transfer._check_file_permissions(cfg, msg)

    assert allowed is False
    assert transport.send_calls
    assert (
        "failed to verify file transfer permissions"
        in transport.send_calls[-1]["message"].text
    )


@pytest.mark.anyio
async def test_check_file_permissions_allows_admin(tmp_path: Path) -> None:
    transport = FakeTransport()
    cfg = make_cfg(transport)
    msg = _msg("/file put", chat_id=-123, chat_type="group")

    allowed = await transfer._check_file_permissions(cfg, msg)

    assert allowed is True
    assert transport.send_calls == []


@pytest.mark.anyio
async def test_save_file_put_requires_document(tmp_path: Path) -> None:
    transport = FakeTransport()
    cfg = replace(make_cfg(transport), runtime=_runtime(tmp_path))
    msg = _msg("/file put")

    result = await transfer._save_file_put(
        cfg,
        msg,
        "note.txt",
        ambient_context=None,
        topic_store=None,
    )

    assert result is None
    assert transport.send_calls
    assert "usage: /file put <path>" in transport.send_calls[-1]["message"].text


@pytest.mark.anyio
async def test_handle_file_put_skips_when_no_save(monkeypatch) -> None:
    transport = FakeTransport()
    cfg = make_cfg(transport)
    msg = _msg("/file put")

    async def _fake_save(*_args, **_kwargs):
        return None

    monkeypatch.setattr(transfer, "_save_file_put", _fake_save)

    await transfer._handle_file_put(
        cfg,
        msg,
        "note.txt",
        ambient_context=None,
        topic_store=None,
    )

    assert transport.send_calls == []


@pytest.mark.anyio
async def test_handle_file_put_group_skips_when_no_save(monkeypatch) -> None:
    transport = FakeTransport()
    cfg = make_cfg(transport)
    msg = _msg("/file put")

    async def _fake_save(*_args, **_kwargs):
        return None

    monkeypatch.setattr(transfer, "_save_file_put_group", _fake_save)

    await transfer._handle_file_put_group(
        cfg,
        msg,
        "uploads/",
        [msg],
        ambient_context=None,
        topic_store=None,
    )

    assert transport.send_calls == []


@pytest.mark.anyio
async def test_handle_file_put_group_infers_dir(tmp_path: Path, monkeypatch) -> None:
    transport = FakeTransport()
    cfg = replace(make_cfg(transport), runtime=_runtime(tmp_path))
    msg = _msg("/file put")
    saved_group = transfer._SavedFilePutGroup(
        context=RunContext(project="proj", branch=None),
        base_dir=None,
        saved=[
            transfer._FilePutResult(
                name="a.txt",
                rel_path=Path("incoming/a.txt"),
                size=1,
                error=None,
            )
        ],
        failed=[],
    )

    async def _fake_save(*_args, **_kwargs):
        return saved_group

    monkeypatch.setattr(transfer, "_save_file_put_group", _fake_save)

    await transfer._handle_file_put_group(
        cfg,
        msg,
        "",
        [msg],
        ambient_context=None,
        topic_store=None,
    )

    assert transport.send_calls
    text = transport.send_calls[-1]["message"].text
    assert "saved a.txt to incoming/" in text


@pytest.mark.anyio
async def test_handle_file_get_permission_denied(tmp_path: Path) -> None:
    transport = FakeTransport()
    files = TelegramFilesSettings(allowed_user_ids=[42])
    cfg = replace(make_cfg(transport), runtime=_runtime(tmp_path), files=files)
    msg = _msg("/file get", sender_id=1)

    await transfer._handle_file_get(
        cfg,
        msg,
        "notes.txt",
        ambient_context=None,
        topic_store=None,
    )

    assert transport.send_calls
    assert "file transfer is not allowed" in transport.send_calls[-1]["message"].text


@pytest.mark.anyio
async def test_handle_file_get_send_failure(tmp_path: Path) -> None:
    class _NoSendBot(FakeBot):
        async def send_document(self, *args, **kwargs):
            _ = args
            _ = kwargs
            return None

    transport = FakeTransport()
    cfg = replace(make_cfg(transport), runtime=_runtime(tmp_path), bot=_NoSendBot())
    target = tmp_path / "notes.txt"
    target.write_text("data", encoding="utf-8")
    msg = _msg("/file get")

    await transfer._handle_file_get(
        cfg,
        msg,
        "notes.txt",
        ambient_context=None,
        topic_store=None,
    )

    assert transport.send_calls
    assert "failed to send file" in transport.send_calls[-1]["message"].text


@pytest.mark.anyio
async def test_save_file_put_reports_invalid_path(tmp_path: Path) -> None:
    transport = FakeTransport()
    cfg = replace(
        make_cfg(transport),
        runtime=_runtime(tmp_path),
        bot=_FileBot(file_info=File(file_path="files/note.txt"), payload=b"hi"),
    )
    msg = _msg("/file put", document=_document(file_name="note.txt"))

    result = await transfer._save_file_put(
        cfg,
        msg,
        "../bad/path",
        ambient_context=None,
        topic_store=None,
    )

    assert result is None
    assert transport.send_calls
    assert "invalid upload path" in transport.send_calls[-1]["message"].text


@pytest.mark.anyio
async def test_save_file_put_reports_document_error(tmp_path: Path) -> None:
    transport = FakeTransport()
    cfg = replace(
        make_cfg(transport),
        runtime=_runtime(tmp_path),
        bot=_FileBot(file_info=None, payload=None),
    )
    msg = _msg("/file put", document=_document(file_name="note.txt"))

    result = await transfer._save_file_put(
        cfg,
        msg,
        "note.txt",
        ambient_context=None,
        topic_store=None,
    )

    assert result is None
    assert transport.send_calls
    assert "failed to fetch file metadata" in transport.send_calls[-1]["message"].text


@pytest.mark.anyio
async def test_save_file_put_reports_missing_path(tmp_path: Path, monkeypatch) -> None:
    transport = FakeTransport()
    cfg = replace(make_cfg(transport), runtime=_runtime(tmp_path))
    msg = _msg("/file put", document=_document(file_name="note.txt"))

    async def _fake_save(*_args, **_kwargs):
        return transfer._FilePutResult(
            name="note.txt",
            rel_path=None,
            size=None,
            error=None,
        )

    monkeypatch.setattr(transfer, "_save_document_payload", _fake_save)

    result = await transfer._save_file_put(
        cfg,
        msg,
        "note.txt",
        ambient_context=None,
        topic_store=None,
    )

    assert result is None
    assert transport.send_calls
    assert "failed to save file" in transport.send_calls[-1]["message"].text


@pytest.mark.anyio
async def test_handle_file_get_requires_context(tmp_path: Path) -> None:
    transport = FakeTransport()
    cfg = make_cfg(transport)
    msg = _msg("/file get")

    await transfer._handle_file_get(
        cfg,
        msg,
        "note.txt",
        ambient_context=None,
        topic_store=None,
    )

    assert transport.send_calls
    assert (
        "no project context available for file download"
        in transport.send_calls[-1]["message"].text
    )


@pytest.mark.anyio
async def test_handle_file_get_denies_path(tmp_path: Path) -> None:
    transport = FakeTransport()
    cfg = replace(make_cfg(transport), runtime=_runtime(tmp_path))
    msg = _msg("/file get")

    await transfer._handle_file_get(
        cfg,
        msg,
        ".env",
        ambient_context=None,
        topic_store=None,
    )

    assert transport.send_calls
    assert "path denied by rule: .env" in transport.send_calls[-1]["message"].text


@pytest.mark.anyio
async def test_handle_file_get_escape_root(tmp_path: Path, monkeypatch) -> None:
    transport = FakeTransport()
    cfg = replace(make_cfg(transport), runtime=_runtime(tmp_path))
    msg = _msg("/file get")

    monkeypatch.setattr(transfer, "resolve_path_within_root", lambda *_a, **_k: None)

    await transfer._handle_file_get(
        cfg,
        msg,
        "note.txt",
        ambient_context=None,
        topic_store=None,
    )

    assert transport.send_calls
    assert (
        "download path escapes the repo root"
        in transport.send_calls[-1]["message"].text
    )


@pytest.mark.anyio
async def test_handle_file_get_zip_too_large(tmp_path: Path, monkeypatch) -> None:
    transport = FakeTransport()
    cfg = replace(make_cfg(transport), runtime=_runtime(tmp_path))
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "file.txt").write_text("data", encoding="utf-8")
    msg = _msg("/file get")

    def _raise(*_args, **_kwargs):
        raise transfer.ZipTooLargeError()

    monkeypatch.setattr(transfer, "zip_directory", _raise)

    await transfer._handle_file_get(
        cfg,
        msg,
        "bundle",
        ambient_context=None,
        topic_store=None,
    )

    assert transport.send_calls
    assert "file is too large to send" in transport.send_calls[-1]["message"].text


@pytest.mark.anyio
async def test_handle_file_get_file_too_large(tmp_path: Path, monkeypatch) -> None:
    transport = FakeTransport()
    cfg = replace(make_cfg(transport), runtime=_runtime(tmp_path))
    target = tmp_path / "notes.txt"
    target.write_bytes(b"data")
    msg = _msg("/file get")

    monkeypatch.setattr(TelegramFilesSettings, "max_download_bytes", 1)

    await transfer._handle_file_get(
        cfg,
        msg,
        "notes.txt",
        ambient_context=None,
        topic_store=None,
    )

    assert transport.send_calls
    assert "file is too large to send" in transport.send_calls[-1]["message"].text
