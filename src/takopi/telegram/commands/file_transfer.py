from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ...config import ConfigError
from ...context import RunContext
from ...directives import DirectiveError
from ...transport_runtime import ResolvedMessage
from ..context import _format_context
from ..files import (
    default_upload_name,
    default_upload_path,
    deny_reason,
    format_bytes,
    normalize_relative_path,
    parse_file_command,
    parse_file_prompt,
    resolve_path_within_root,
    write_bytes_atomic,
    ZipTooLargeError,
    zip_directory,
)
from ..topic_state import TopicStateStore
from ..topics import _maybe_update_topic_context, _topic_key
from ..types import TelegramDocument, TelegramIncomingMessage
from .reply import make_reply

if TYPE_CHECKING:
    from ..bridge import TelegramBridgeConfig

FILE_PUT_USAGE = "usage: `/file put <path>`"
FILE_GET_USAGE = "usage: `/file get <path>`"


@dataclass(slots=True)
class _FilePutPlan:
    resolved: ResolvedMessage
    run_root: Path
    path_value: str | None
    force: bool


@dataclass(slots=True)
class _FilePutResult:
    name: str
    rel_path: Path | None
    size: int | None
    error: str | None


@dataclass(slots=True)
class _SavedFilePut:
    context: RunContext | None
    rel_path: Path
    size: int


@dataclass(slots=True)
class _SavedFilePutGroup:
    context: RunContext | None
    base_dir: Path | None
    saved: list[_FilePutResult]
    failed: list[_FilePutResult]


def resolve_file_put_paths(
    plan: _FilePutPlan,
    *,
    cfg: TelegramBridgeConfig,
    require_dir: bool,
) -> tuple[Path | None, Path | None, str | None]:
    path_value = plan.path_value
    if not path_value:
        return None, None, None
    if require_dir or path_value.endswith("/"):
        base_dir = normalize_relative_path(path_value)
        if base_dir is None:
            return None, None, "invalid upload path."
        deny_rule = deny_reason(base_dir, cfg.files.deny_globs)
        if deny_rule is not None:
            return None, None, f"path denied by rule: {deny_rule}"
        base_target = resolve_path_within_root(plan.run_root, base_dir)
        if base_target is None:
            return None, None, "upload path escapes the repo root."
        if base_target.exists() and not base_target.is_dir():
            return None, None, "upload path is a file."
        return base_dir, None, None
    rel_path = normalize_relative_path(path_value)
    if rel_path is None:
        return None, None, "invalid upload path."
    return None, rel_path, None


async def _check_file_permissions(
    cfg: TelegramBridgeConfig, msg: TelegramIncomingMessage
) -> bool:
    reply = make_reply(cfg, msg)
    sender_id = msg.sender_id
    if sender_id is None:
        await reply(text="cannot verify sender for file transfer.")
        return False
    if cfg.files.allowed_user_ids:
        if sender_id not in cfg.files.allowed_user_ids:
            await reply(text="file transfer is not allowed for this user.")
            return False
        return True
    if msg.is_private:
        return True
    member = await cfg.bot.get_chat_member(msg.chat_id, sender_id)
    if member is None:
        await reply(text="failed to verify file transfer permissions.")
        return False
    if member.status in {"creator", "administrator"}:
        return True
    await reply(text="file transfer is restricted to group admins.")
    return False


async def _prepare_file_put_plan(
    cfg: TelegramBridgeConfig,
    msg: TelegramIncomingMessage,
    args_text: str,
    ambient_context: RunContext | None,
    topic_store: TopicStateStore | None,
) -> _FilePutPlan | None:
    reply = make_reply(cfg, msg)
    if not await _check_file_permissions(cfg, msg):
        return None
    try:
        resolved = cfg.runtime.resolve_message(
            text=args_text,
            reply_text=msg.reply_to_text,
            ambient_context=ambient_context,
            chat_id=msg.chat_id,
        )
    except DirectiveError as exc:
        await reply(text=f"error:\n{exc}")
        return None
    topic_key = _topic_key(msg, cfg) if topic_store is not None else None
    await _maybe_update_topic_context(
        cfg=cfg,
        topic_store=topic_store,
        topic_key=topic_key,
        context=resolved.context,
        context_source=resolved.context_source,
    )
    if resolved.context is None or resolved.context.project is None:
        await reply(text="no project context available for file upload.")
        return None
    try:
        run_root = cfg.runtime.resolve_run_cwd(resolved.context)
    except ConfigError as exc:
        await reply(text=f"error:\n{exc}")
        return None
    if run_root is None:
        await reply(text="no project context available for file upload.")
        return None
    path_value, force, error = parse_file_prompt(resolved.prompt, allow_empty=True)
    if error is not None:
        await reply(text=error)
        return None
    return _FilePutPlan(
        resolved=resolved,
        run_root=run_root,
        path_value=path_value,
        force=force,
    )


def _format_file_put_failures(failed: Sequence[_FilePutResult]) -> str | None:
    if not failed:
        return None
    errors = ", ".join(
        f"`{item.name}` ({item.error})" for item in failed if item.error is not None
    )
    if not errors:
        return None
    return f"failed: {errors}"


async def _save_document_payload(
    cfg: TelegramBridgeConfig,
    *,
    document: TelegramDocument,
    run_root: Path,
    rel_path: Path | None,
    base_dir: Path | None,
    force: bool,
) -> _FilePutResult:
    name = default_upload_name(document.file_name, None)
    if (
        document.file_size is not None
        and document.file_size > cfg.files.max_upload_bytes
    ):
        return _FilePutResult(
            name=name,
            rel_path=None,
            size=None,
            error="file is too large to upload.",
        )
    file_info = await cfg.bot.get_file(document.file_id)
    if file_info is None:
        return _FilePutResult(
            name=name,
            rel_path=None,
            size=None,
            error="failed to fetch file metadata.",
        )
    file_path = file_info.file_path
    name = default_upload_name(document.file_name, file_path)
    resolved_path = rel_path
    if resolved_path is None:
        if base_dir is None:
            resolved_path = default_upload_path(
                cfg.files.uploads_dir, document.file_name, file_path
            )
        else:
            resolved_path = base_dir / name
    deny_rule = deny_reason(resolved_path, cfg.files.deny_globs)
    if deny_rule is not None:
        return _FilePutResult(
            name=name,
            rel_path=None,
            size=None,
            error=f"path denied by rule: {deny_rule}",
        )
    target = resolve_path_within_root(run_root, resolved_path)
    if target is None:
        return _FilePutResult(
            name=name,
            rel_path=None,
            size=None,
            error="upload path escapes the repo root.",
        )
    if target.exists():
        if target.is_dir():
            return _FilePutResult(
                name=name,
                rel_path=None,
                size=None,
                error="upload target is a directory.",
            )
        if not force:
            return _FilePutResult(
                name=name,
                rel_path=None,
                size=None,
                error="file already exists; use --force to overwrite.",
            )
    payload = await cfg.bot.download_file(file_path)
    if payload is None:
        return _FilePutResult(
            name=name,
            rel_path=None,
            size=None,
            error="failed to download file.",
        )
    if len(payload) > cfg.files.max_upload_bytes:
        return _FilePutResult(
            name=name,
            rel_path=None,
            size=None,
            error="file is too large to upload.",
        )
    try:
        write_bytes_atomic(target, payload)
    except OSError as exc:
        return _FilePutResult(
            name=name,
            rel_path=None,
            size=None,
            error=f"failed to write file: {exc}",
        )
    return _FilePutResult(
        name=name,
        rel_path=resolved_path,
        size=len(payload),
        error=None,
    )


async def _handle_file_command(
    cfg: TelegramBridgeConfig,
    msg: TelegramIncomingMessage,
    args_text: str,
    ambient_context: RunContext | None,
    topic_store: TopicStateStore | None,
) -> None:
    reply = make_reply(cfg, msg)
    command, rest, error = parse_file_command(args_text)
    if error is not None:
        await reply(text=error)
        return
    if command == "put":
        await _handle_file_put(cfg, msg, rest, ambient_context, topic_store)
    else:
        await _handle_file_get(cfg, msg, rest, ambient_context, topic_store)


async def _handle_file_put_default(
    cfg: TelegramBridgeConfig,
    msg: TelegramIncomingMessage,
    ambient_context: RunContext | None,
    topic_store: TopicStateStore | None,
) -> None:
    await _handle_file_put(cfg, msg, "", ambient_context, topic_store)


async def _save_file_put(
    cfg: TelegramBridgeConfig,
    msg: TelegramIncomingMessage,
    args_text: str,
    ambient_context: RunContext | None,
    topic_store: TopicStateStore | None,
) -> _SavedFilePut | None:
    reply = make_reply(cfg, msg)
    document = msg.document
    if document is None:
        await reply(text=FILE_PUT_USAGE)
        return None
    plan = await _prepare_file_put_plan(
        cfg,
        msg,
        args_text,
        ambient_context,
        topic_store,
    )
    if plan is None:
        return None
    base_dir, rel_path, error = resolve_file_put_paths(
        plan,
        cfg=cfg,
        require_dir=False,
    )
    if error is not None:
        await reply(text=error)
        return None
    result = await _save_document_payload(
        cfg,
        document=document,
        run_root=plan.run_root,
        rel_path=rel_path,
        base_dir=base_dir,
        force=plan.force,
    )
    if result.error is not None:
        await reply(text=result.error)
        return None
    if result.rel_path is None or result.size is None:
        await reply(text="failed to save file.")
        return None
    return _SavedFilePut(
        context=plan.resolved.context,
        rel_path=result.rel_path,
        size=result.size,
    )


async def _handle_file_put(
    cfg: TelegramBridgeConfig,
    msg: TelegramIncomingMessage,
    args_text: str,
    ambient_context: RunContext | None,
    topic_store: TopicStateStore | None,
) -> None:
    reply = make_reply(cfg, msg)
    saved = await _save_file_put(
        cfg,
        msg,
        args_text,
        ambient_context,
        topic_store,
    )
    if saved is None:
        return
    context_label = _format_context(cfg.runtime, saved.context)
    await reply(
        text=(
            f"saved `{saved.rel_path.as_posix()}` "
            f"in `{context_label}` ({format_bytes(saved.size)})"
        ),
    )


async def _handle_file_put_group(
    cfg: TelegramBridgeConfig,
    msg: TelegramIncomingMessage,
    args_text: str,
    messages: Sequence[TelegramIncomingMessage],
    ambient_context: RunContext | None,
    topic_store: TopicStateStore | None,
) -> None:
    reply = make_reply(cfg, msg)
    saved_group = await _save_file_put_group(
        cfg,
        msg,
        args_text,
        messages,
        ambient_context,
        topic_store,
    )
    if saved_group is None:
        return
    context_label = _format_context(cfg.runtime, saved_group.context)
    total_bytes = sum(item.size or 0 for item in saved_group.saved)
    dir_label: Path | None = saved_group.base_dir
    if dir_label is None and saved_group.saved:
        first_path = saved_group.saved[0].rel_path
        if first_path is not None:
            dir_label = first_path.parent
    if saved_group.saved:
        saved_names = ", ".join(f"`{item.name}`" for item in saved_group.saved)
        if dir_label is not None:
            dir_text = dir_label.as_posix()
            if not dir_text.endswith("/"):
                dir_text = f"{dir_text}/"
            text = (
                f"saved {saved_names} to `{dir_text}` "
                f"in `{context_label}` ({format_bytes(total_bytes)})"
            )
        else:
            text = (
                f"saved {saved_names} in `{context_label}` "
                f"({format_bytes(total_bytes)})"
            )
    else:
        text = "failed to upload files."
    failure_text = _format_file_put_failures(saved_group.failed)
    if failure_text is not None:
        text = f"{text}\n\n{failure_text}"
    await reply(text=text)


async def _save_file_put_group(
    cfg: TelegramBridgeConfig,
    msg: TelegramIncomingMessage,
    args_text: str,
    messages: Sequence[TelegramIncomingMessage],
    ambient_context: RunContext | None,
    topic_store: TopicStateStore | None,
) -> _SavedFilePutGroup | None:
    reply = make_reply(cfg, msg)
    documents = [item.document for item in messages if item.document is not None]
    if not documents:
        await reply(text=FILE_PUT_USAGE)
        return None
    plan = await _prepare_file_put_plan(
        cfg,
        msg,
        args_text,
        ambient_context,
        topic_store,
    )
    if plan is None:
        return None
    base_dir, _, error = resolve_file_put_paths(
        plan,
        cfg=cfg,
        require_dir=True,
    )
    if error is not None:
        await reply(text=error)
        return None
    saved: list[_FilePutResult] = []
    failed: list[_FilePutResult] = []
    for document in documents:
        result = await _save_document_payload(
            cfg,
            document=document,
            run_root=plan.run_root,
            rel_path=None,
            base_dir=base_dir,
            force=plan.force,
        )
        if result.error is None:
            saved.append(result)
        else:
            failed.append(result)
    return _SavedFilePutGroup(
        context=plan.resolved.context,
        base_dir=base_dir,
        saved=saved,
        failed=failed,
    )


async def _handle_file_get(
    cfg: TelegramBridgeConfig,
    msg: TelegramIncomingMessage,
    args_text: str,
    ambient_context: RunContext | None,
    topic_store: TopicStateStore | None,
) -> None:
    reply = make_reply(cfg, msg)
    if not await _check_file_permissions(cfg, msg):
        return
    try:
        resolved = cfg.runtime.resolve_message(
            text=args_text,
            reply_text=msg.reply_to_text,
            ambient_context=ambient_context,
            chat_id=msg.chat_id,
        )
    except DirectiveError as exc:
        await reply(text=f"error:\n{exc}")
        return
    topic_key = _topic_key(msg, cfg) if topic_store is not None else None
    await _maybe_update_topic_context(
        cfg=cfg,
        topic_store=topic_store,
        topic_key=topic_key,
        context=resolved.context,
        context_source=resolved.context_source,
    )
    if resolved.context is None or resolved.context.project is None:
        await reply(text="no project context available for file download.")
        return
    try:
        run_root = cfg.runtime.resolve_run_cwd(resolved.context)
    except ConfigError as exc:
        await reply(text=f"error:\n{exc}")
        return
    if run_root is None:
        await reply(text="no project context available for file download.")
        return
    path_value = resolved.prompt
    if not path_value.strip():
        await reply(text=FILE_GET_USAGE)
        return
    rel_path = normalize_relative_path(path_value)
    if rel_path is None:
        await reply(text="invalid download path.")
        return
    deny_rule = deny_reason(rel_path, cfg.files.deny_globs)
    if deny_rule is not None:
        await reply(text=f"path denied by rule: {deny_rule}")
        return
    target = resolve_path_within_root(run_root, rel_path)
    if target is None:
        await reply(text="download path escapes the repo root.")
        return
    if not target.exists():
        await reply(text="file does not exist.")
        return
    if target.is_dir():
        try:
            payload = zip_directory(
                run_root,
                rel_path,
                cfg.files.deny_globs,
                max_bytes=cfg.files.max_download_bytes,
            )
        except ZipTooLargeError:
            await reply(text="file is too large to send.")
            return
        except OSError as exc:
            await reply(text=f"failed to read directory: {exc}")
            return
        filename = f"{rel_path.name or 'archive'}.zip"
    else:
        try:
            size = target.stat().st_size
            if size > cfg.files.max_download_bytes:
                await reply(text="file is too large to send.")
                return
            payload = target.read_bytes()
        except OSError as exc:
            await reply(text=f"failed to read file: {exc}")
            return
        filename = target.name
    if len(payload) > cfg.files.max_download_bytes:
        await reply(text="file is too large to send.")
        return
    sent = await cfg.bot.send_document(
        chat_id=msg.chat_id,
        filename=filename,
        content=payload,
        reply_to_message_id=msg.message_id,
        message_thread_id=msg.thread_id,
    )
    if sent is None:
        await reply(text="failed to send file.")
        return
